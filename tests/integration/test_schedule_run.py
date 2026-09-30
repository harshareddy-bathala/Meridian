"""``meridian.scheduler.run`` against real TimescaleDB.

Passes are inserted directly with chosen acquisitions rather than propagated,
so each test states the collision it is about: the scheduling decision is what
is under test here, not the geometry that produced the candidates.

The real propagator is used for timing uncertainty, which needs no propagation
— it reads the element set's age — so the windows written are the ones a real
run would write.

Uses the ``rollback`` fixture pattern established in ``test_store_invites.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from meridian.orbit.skyfield_service import SkyfieldOrbitService  # noqa: E402
from meridian.orbit.uncertainty import timing_uncertainty_at_age  # noqa: E402
from meridian.prediction.live import LiveScorer  # noqa: E402
from meridian.prediction.score import Linear, Model, sigmoid  # noqa: E402
from meridian.scheduler import ScheduleOutcome  # noqa: E402
from meridian.scheduler import run as run_module  # noqa: E402
from meridian.scheduler.assignment_records import assignment_id_for  # noqa: E402
from meridian.scheduler.optimiser import Optimised, SolverRun  # noqa: E402
from meridian.scheduler.run import (  # noqa: E402
    SCHEDULER_LOCK,
    ScheduleInvalidError,
    ScheduleRequest,
    run_schedule,
)
from meridian.scheduler.schedule_config import (  # noqa: E402
    ScheduleConfig,
    schedule_config_sha256,
)
from meridian.store.assignment_log import find_assignments  # noqa: E402
from meridian.store.revocations import revoke_declined  # noqa: E402

pytestmark = pytest.mark.integration

STATION = "st_sched"
METEOR = "norad:57166"
CUBESAT = "norad:99123"
METEOR_HZ = 137_100_000
CUBESAT_HZ = 137_620_000

HORIZON_START = datetime(2026, 8, 14, tzinfo=UTC)
HORIZON_END = HORIZON_START + timedelta(days=1)
EPOCH = HORIZON_START - timedelta(days=1)
"""One day before the horizon, so the timing prior is a known 0.533 s."""

LINE1 = "1 57166U 23091A   26220.09250000  .00000098  00000-0  61234-4 0  9995"
LINE2 = "2 57166  98.7123 201.3345 0002145  85.1234 275.0123 14.22150000123456"


def a_request(
    model_config: str = "A", model: str | None = None, *, now: datetime | None = None
) -> ScheduleRequest:
    """One run over the whole horizon, for a station that does not slew."""
    return ScheduleRequest(
        start=HORIZON_START,
        end=HORIZON_END,
        now=datetime.now(UTC) if now is None else now,
        config=ScheduleConfig(configuration=model_config, model=model),
    )


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    """Undo everything this test writes — see test_store_invites.py's twin."""
    with conn.transaction(force_rollback=True):
        yield conn


def _insert_satellite(
    cur: Any, satellite_id: str, freq_hz: int, priority: float
) -> int:
    """One satellite with one downlink and one element set, returning the set id."""
    cur.execute(
        "insert into satellites (satellite_id, name, priority) values (%s, %s, %s)",
        (satellite_id, satellite_id, priority),
    )
    cur.execute(
        "insert into satellite_transmitters (satellite_id, centre_freq_hz, mode)"
        " values (%s, %s, 'lrpt')",
        (satellite_id, freq_hz),
    )
    cur.execute(
        "insert into element_sets (satellite_id, epoch, line1, line2, source)"
        " values (%s, %s, %s, %s, 'manual') returning id",
        (satellite_id, EPOCH, LINE1, LINE2.replace("57166", satellite_id[-5:])),
    )
    (element_set_id,) = cur.fetchone()
    return int(element_set_id)


def _insert_pass(
    cur: Any,
    element_set_id: int,
    *,
    satellite_id: str,
    at_minute: float,
    max_elevation_deg: float,
) -> int:
    """One eleven-minute pass starting ``at_minute`` into the horizon."""
    aos = HORIZON_START + timedelta(minutes=at_minute)
    cur.execute(
        "insert into passes (satellite_id, station_id, aos, los, max_elevation_deg,"
        " max_elevation_at, aos_azimuth_deg, los_azimuth_deg, element_set_id,"
        " min_elevation_deg, simulated)"
        " values (%s, %s, %s, %s, %s, %s, 10, 200, %s, 10, false) returning id",
        (
            satellite_id,
            STATION,
            aos,
            aos + timedelta(minutes=11),
            max_elevation_deg,
            aos + timedelta(minutes=5),
            element_set_id,
        ),
    )
    (pass_id,) = cur.fetchone()
    return int(pass_id)


@pytest.fixture
def network(rollback: Any) -> dict[str, int]:
    """One station on VHF, two satellites it can receive, no passes yet."""
    with rollback.cursor() as cur:
        cur.execute(
            "insert into stations (station_id, name, operator, lat_deg, lon_deg,"
            " alt_m, token_sha256, registration_key_sha256)"
            " values (%s, 'S', 'tests', 17.4, 78.5, 542, %s, %s)",
            (STATION, bytes([7]) * 32, bytes([8]) * 32),
        )
        cur.execute(
            "insert into station_capabilities (station_id, band, freq_min_hz,"
            " freq_max_hz, modes, polarisation, min_elevation_deg)"
            " values (%s, 'vhf', 136000000, 138000000, '{lrpt}', 'rhcp', 10)",
            (STATION,),
        )
        return {
            METEOR: _insert_satellite(cur, METEOR, METEOR_HZ, priority=1.0),
            CUBESAT: _insert_satellite(cur, CUBESAT, CUBESAT_HZ, priority=1.0),
        }


def _decisions(conn: Any, model_config: str = "A") -> list[tuple[Any, ...]]:
    """Every decision this configuration made, ordered by acquisition."""
    with conn.cursor() as cur:
        cur.execute(
            "select a.pass_id, a.decision, a.score, a.conflicts_with_assignment_id,"
            " a.start_at, a.end_at, a.centre_freq_hz, a.simulated, a.reason"
            " from assignments a join passes p on p.id = a.pass_id"
            " where a.model_config = %s order by p.aos",
            (model_config,),
        )
        return cur.fetchall()


def test_non_overlapping_passes_are_all_scheduled(
    rollback: Any, network: dict[str, int]
) -> None:
    """Nothing collides, so nothing is skipped."""
    with rollback.cursor() as cur:
        _insert_pass(
            cur, network[METEOR], satellite_id=METEOR, at_minute=0, max_elevation_deg=40
        )
        _insert_pass(
            cur,
            network[METEOR],
            satellite_id=METEOR,
            at_minute=90,
            max_elevation_deg=25,
        )

    report = run_schedule(rollback, SkyfieldOrbitService(), a_request())

    assert report.candidates_considered == 2
    assert report.scheduled == 2
    assert report.skipped == 0
    assert [row[1] for row in _decisions(rollback)] == ["scheduled", "scheduled"]


def test_an_overlap_leaves_one_selection_and_one_skip_naming_it(
    rollback: Any, network: dict[str, int]
) -> None:
    """The screen PROJECT.md section 13 calls "the entire project", as rows.

    The higher pass wins under configuration A, and the loser records which
    decision took its slot rather than merely that it lost.
    """
    with rollback.cursor() as cur:
        winner = _insert_pass(
            cur, network[METEOR], satellite_id=METEOR, at_minute=0, max_elevation_deg=70
        )
        _insert_pass(
            cur,
            network[CUBESAT],
            satellite_id=CUBESAT,
            at_minute=5,
            max_elevation_deg=20,
        )

    report = run_schedule(rollback, SkyfieldOrbitService(), a_request())

    assert (report.scheduled, report.skipped) == (1, 1)
    rows = _decisions(rollback)
    assert [row[1] for row in rows] == ["scheduled", "skipped"]
    assert rows[1][3] == assignment_id_for(winner, "A")
    assert "already committed" in rows[1][8]


def test_the_assignment_window_is_wider_than_the_pass(
    rollback: Any, network: dict[str, int]
) -> None:
    """D-021: an assignment's window is the pass opened out by the 1σ prior.

    A station told to record from exactly the predicted acquisition starts after
    a pass whose element set was stale has already begun, and loses the rise —
    the part that most often decides whether the decode locks. The expected
    margin is computed from the element set's age rather than read back from the
    row, so a run that widened by nothing, or by a fixed pad, fails here.
    """
    with rollback.cursor() as cur:
        _insert_pass(
            cur, network[METEOR], satellite_id=METEOR, at_minute=0, max_elevation_deg=40
        )

    run_schedule(rollback, SkyfieldOrbitService(), a_request())

    expected_margin_s = timing_uncertainty_at_age(timedelta(days=1).total_seconds())
    start_at, end_at = _decisions(rollback)[0][4], _decisions(rollback)[0][5]

    assert start_at == HORIZON_START - timedelta(seconds=expected_margin_s.sigma_s)
    assert end_at == HORIZON_START + timedelta(minutes=11) + timedelta(
        seconds=expected_margin_s.sigma_s
    )


def test_running_one_configuration_twice_writes_nothing_the_second_time(
    rollback: Any, network: dict[str, int]
) -> None:
    """Two scheduled assignments for one pass means a station told twice.

    MSP section 4.2's reconciliation would then have two ids for one reception.
    The decision id is derived from the pass and the configuration, so the
    repeat collapses onto assignment_decision_unique (D-066).
    """
    with rollback.cursor() as cur:
        _insert_pass(
            cur, network[METEOR], satellite_id=METEOR, at_minute=0, max_elevation_deg=40
        )

    first = run_schedule(rollback, SkyfieldOrbitService(), a_request())
    second = run_schedule(rollback, SkyfieldOrbitService(), a_request())

    assert first.rows_written == 1
    assert (second.candidates_considered, second.already_decided) == (0, 1)
    assert second.rows_written == 0
    assert len(_decisions(rollback)) == 1


def test_a_second_configuration_decides_around_the_first_ones_assignments(
    rollback: Any, network: dict[str, int]
) -> None:
    """A station has one antenna, whichever configuration asked (D-165).

    B still records its own decision about the pass — keyed on the pass alone
    it would have written nothing — but it is a skip naming A's assignment,
    not a second assignment for the same reception. Configurations are
    compared by replay (D-172), not by delivering two schedules.
    """
    with rollback.cursor() as cur:
        pass_id = _insert_pass(
            cur, network[METEOR], satellite_id=METEOR, at_minute=0, max_elevation_deg=40
        )

    run_schedule(rollback, SkyfieldOrbitService(), a_request("A"))
    run_schedule(rollback, SkyfieldOrbitService(), a_request("B"))

    assert [row[1] for row in _decisions(rollback, "A")] == ["scheduled"]
    b_rows = _decisions(rollback, "B")
    assert [row[1] for row in b_rows] == ["skipped"]
    assert b_rows[0][3] == assignment_id_for(pass_id, "A")


def test_priority_changes_which_pass_b_takes_and_leaves_a_alone(
    rollback: Any, network: dict[str, int]
) -> None:
    """The one difference between the two baselines, isolated.

    Two overlapping passes: the cubesat's is lower but its satellite is weighted
    at three. Configuration A takes the higher pass on geometry; configuration B
    takes the cubesat, 30 × 3 = 90 against 70. Nothing else about the two runs
    differs, so this is the priority weighting and nothing else.
    """
    with rollback.cursor() as cur:
        cur.execute(
            "update satellites set priority = 3.0 where satellite_id = %s", (CUBESAT,)
        )
        higher = _insert_pass(
            cur, network[METEOR], satellite_id=METEOR, at_minute=0, max_elevation_deg=70
        )
        weighted = _insert_pass(
            cur,
            network[CUBESAT],
            satellite_id=CUBESAT,
            at_minute=5,
            max_elevation_deg=30,
        )

    taken = {}
    for model_config in ("A", "B"):
        # Each configuration on the network as it stood, not around the other's
        # assignments, so the two differ in the ranking and nothing else.
        with rollback.transaction(force_rollback=True):
            run_schedule(rollback, SkyfieldOrbitService(), a_request(model_config))
            taken[model_config] = [
                row[0]
                for row in _decisions(rollback, model_config)
                if row[1] == "scheduled"
            ]
    taken_by_a, taken_by_b = taken["A"], taken["B"]

    assert taken_by_a == [higher]
    assert taken_by_b == [weighted]


def test_the_score_stored_is_the_one_the_decision_was_made_with(
    rollback: Any, network: dict[str, int]
) -> None:
    """The value the optimiser weighed, stored as the score (D-168).

    A 45° pass of eleven minutes under the elevation proxy is worth
    45/90 × 660 s = 330; B weights that by the satellite's priority of two.
    """
    with rollback.cursor() as cur:
        cur.execute(
            "update satellites set priority = 2.0 where satellite_id = %s", (METEOR,)
        )
        _insert_pass(
            cur, network[METEOR], satellite_id=METEOR, at_minute=0, max_elevation_deg=45
        )

    scores = {}
    for model_config in ("A", "B"):
        with rollback.transaction(force_rollback=True):
            run_schedule(rollback, SkyfieldOrbitService(), a_request(model_config))
            (row,) = _decisions(rollback, model_config)
            scores[model_config] = (row[1], row[2])

    assert scores == {"A": ("scheduled", 330.0), "B": ("scheduled", 660.0)}


def _round(conn: Any, start_minute: float, length_minutes: float) -> Any:
    """One jobs-service round over ``[start, start + length)``, in minutes."""
    start = HORIZON_START + timedelta(minutes=start_minute)
    return run_schedule(
        conn,
        SkyfieldOrbitService(),
        ScheduleRequest(
            start=start,
            end=start + timedelta(minutes=length_minutes),
            now=datetime.now(UTC),
            config=ScheduleConfig(),
        ),
    )


def test_a_later_round_never_schedules_on_top_of_an_earlier_ones_assignment(
    rollback: Any, network: dict[str, int]
) -> None:
    """D-110's rounds overlap, and the tail of each is new (D-165).

    The first round, over the first hour, takes the pass rising at minute 50.
    The second, five minutes later, sees a higher pass rising at minute 60 —
    outside the first round's horizon, inside its own, and overlapping the
    first pass by a minute. Before D-165 the second round ranked only its own
    candidates, took the newcomer, and left two overlapping assignments. The
    pass at minute 90 is the positive control: new, clear, and taken.
    """
    with rollback.cursor() as cur:
        first = _insert_pass(
            cur,
            network[METEOR],
            satellite_id=METEOR,
            at_minute=50,
            max_elevation_deg=30,
        )
    _round(rollback, 0, 60)

    with rollback.cursor() as cur:
        newcomer = _insert_pass(
            cur,
            network[CUBESAT],
            satellite_id=CUBESAT,
            at_minute=60,
            max_elevation_deg=80,
        )
        clear = _insert_pass(
            cur,
            network[CUBESAT],
            satellite_id=CUBESAT,
            at_minute=90,
            max_elevation_deg=20,
        )
    second = _round(rollback, 5, 120)

    decisions = {row[0]: row for row in _decisions(rollback)}
    assert second.already_decided == 1
    assert decisions[first][1] == "scheduled"
    assert decisions[newcomer][1] == "skipped"
    assert decisions[newcomer][3] == assignment_id_for(first, "A")
    assert decisions[clear][1] == "scheduled"


def test_passes_that_only_touch_conflict_once_widened(
    rollback: Any, network: dict[str, int]
) -> None:
    """D-166: overlap is judged on the assignment windows the station records.

    The second pass rises the instant the first sets. On the passes alone they
    are compatible; each window is opened out by the element set's 0.533 s, so
    the station would have to be recording both for a second.
    """
    with rollback.cursor() as cur:
        first = _insert_pass(
            cur, network[METEOR], satellite_id=METEOR, at_minute=0, max_elevation_deg=60
        )
        second = _insert_pass(
            cur,
            network[CUBESAT],
            satellite_id=CUBESAT,
            at_minute=11,
            max_elevation_deg=30,
        )

    run_schedule(rollback, SkyfieldOrbitService(), a_request())

    decisions = {row[0]: row for row in _decisions(rollback)}
    assert decisions[first][1] == "scheduled"
    assert decisions[second][1] == "skipped"
    assert decisions[second][3] == assignment_id_for(first, "A")


def test_a_ninth_assignment_one_heartbeat_would_carry_is_skipped(
    rollback: Any, network: dict[str, int]
) -> None:
    """D-035's cap, enforced where assignments are made (D-166).

    Nine passes a quarter of an hour apart never overlap, and are all eligible
    for delivery at once. The lowest is skipped, naming no assignment.
    """
    with rollback.cursor() as cur:
        passes = [
            _insert_pass(
                cur,
                network[METEOR],
                satellite_id=METEOR,
                at_minute=number * 12,
                max_elevation_deg=80 - number,
            )
            for number in range(9)
        ]

    report = run_schedule(rollback, SkyfieldOrbitService(), a_request())

    decisions = {row[0]: row for row in _decisions(rollback)}
    assert (report.scheduled, report.skipped) == (8, 1)
    assert decisions[passes[-1]][1] == "skipped"
    assert decisions[passes[-1]][3] is None
    assert "D-035" in decisions[passes[-1]][8]


def test_an_offline_station_is_given_nothing_and_decided_when_back(
    rollback: Any, network: dict[str, int]
) -> None:
    """D-166: offline withholds new work, and defers the decision rather than
    skipping, so the passes are still open when the station returns."""
    with rollback.cursor() as cur:
        pass_id = _insert_pass(
            cur, network[METEOR], satellite_id=METEOR, at_minute=0, max_elevation_deg=40
        )
        cur.execute(
            "update stations set last_heartbeat_at = now() - interval '10 minutes'"
            " where station_id = %s",
            (STATION,),
        )

    away = run_schedule(rollback, SkyfieldOrbitService(), a_request())

    assert away.stations_unavailable == (STATION,)
    assert (away.passes_deferred, away.rows_written) == (1, 0)
    assert _decisions(rollback) == []

    with rollback.cursor() as cur:
        cur.execute(
            "update stations set last_heartbeat_at = now() where station_id = %s",
            (STATION,),
        )
    back = run_schedule(rollback, SkyfieldOrbitService(), a_request())

    assert back.stations_unavailable == ()
    assert [(row[0], row[1]) for row in _decisions(rollback)] == [
        (pass_id, "scheduled")
    ]


def test_simulated_survives_from_the_station_through_to_the_assignment(
    rollback: Any, network: dict[str, int]
) -> None:
    """CLAUDE.md rule 5 — the flag is labelled at every layer.

    Nothing would fail loudly if the scheduler defaulted it, and every simulated
    assignment would then be indistinguishable from a measured one.
    """
    with rollback.cursor() as cur:
        cur.execute(
            "update stations set simulated = true, simulator_run_id = 'r',"
            " seed = 1 where station_id = %s",
            (STATION,),
        )
        cur.execute(
            "insert into passes (satellite_id, station_id, aos, los,"
            " max_elevation_deg, max_elevation_at, aos_azimuth_deg,"
            " los_azimuth_deg, element_set_id, min_elevation_deg, simulated)"
            " values (%s, %s, %s, %s, 40, %s, 10, 200, %s, 10, true)",
            (
                METEOR,
                STATION,
                HORIZON_START,
                HORIZON_START + timedelta(minutes=11),
                HORIZON_START + timedelta(minutes=5),
                network[METEOR],
            ),
        )

    run_schedule(rollback, SkyfieldOrbitService(), a_request())

    assert _decisions(rollback)[0][7] is True


def test_a_model_configured_and_not_loaded_is_refused(
    rollback: Any, network: dict[str, int]
) -> None:
    """A run labelled D that no model scored would publish a number under a
    label it did not earn; so would one scored by a model nobody configured."""
    assert network
    with pytest.raises(ValueError, match="scorer must be given exactly when"):
        run_schedule(rollback, SkyfieldOrbitService(), a_request("D", "models/d"))
    with pytest.raises(ValueError, match="scorer must be given exactly when"):
        run_schedule(rollback, SkyfieldOrbitService(), a_request(), a_scorer("A"))


def test_another_configuration_s_model_is_refused(
    rollback: Any, network: dict[str, int]
) -> None:
    assert network
    with pytest.raises(ValueError, match="is scored by a model of"):
        run_schedule(
            rollback,
            SkyfieldOrbitService(),
            a_request("C", "models/a"),
            a_scorer("A"),
        )


def test_a_naive_horizon_is_refused(rollback: Any, network: dict[str, int]) -> None:
    """CLAUDE.local.md section 6: a naive datetime is a bug, not a tolerance."""
    assert network
    naive = ScheduleRequest(
        start=HORIZON_START.replace(tzinfo=None),
        end=HORIZON_END,
        now=datetime.now(UTC),
        config=ScheduleConfig(),
    )
    with pytest.raises(ValueError, match="naive"):
        run_schedule(rollback, SkyfieldOrbitService(), naive)


# --- the optimiser, the run, and the explanation (D-167, D-170) ---------------


def a_scorer(configuration: str) -> LiveScorer:
    """A model reading peak elevation alone, written by hand: sigmoid((e − 30)/10)."""
    linear = Linear(
        features=("max_elevation_deg",),
        mean=(30.0,),
        scale=(10.0,),
        coefficients=(1.0,),
        intercept=0.0,
        calibration_a=1.0,
        calibration_b=0.0,
    )
    model = Model(
        configuration=configuration,
        reads_history=False,
        min_station_history=0,
        configured=linear,
        fallback=None,
    )
    return LiveScorer(model, bytes([configuration.encode()[0]]) * 32)


def _run_record(conn: Any, run_id: str) -> tuple[Any, ...]:
    with conn.cursor() as cur:
        cur.execute(
            "select status, yield_source, model_sha256, history_as_of, candidates,"
            " scheduled, skipped, config_sha256, solver, objective"
            " from schedule_runs where run_id = %s",
            (run_id,),
        )
        return cur.fetchone()


def _explained(conn: Any) -> dict[int, tuple[Any, ...]]:
    with conn.cursor() as cur:
        cur.execute(
            "select pass_id, schedule_run_id, explanation, predicted_yield,"
            " model_sha256 from assignments"
        )
        return {row[0]: row[1:] for row in cur.fetchall()}


def test_the_optimiser_takes_two_passes_greedy_would_trade_for_one(
    rollback: Any, network: dict[str, int]
) -> None:
    """One high pass overlapping two lower ones that do not overlap each other.

    Greedy takes the high pass, worth 70/90 × 660 = 513; the two lower ones
    are worth 330 each, 660 together, and the optimiser takes them.
    """
    with rollback.cursor() as cur:
        low_early = _insert_pass(
            cur, network[METEOR], satellite_id=METEOR, at_minute=0, max_elevation_deg=45
        )
        high = _insert_pass(
            cur,
            network[CUBESAT],
            satellite_id=CUBESAT,
            at_minute=10,
            max_elevation_deg=70,
        )
        low_late = _insert_pass(
            cur,
            network[METEOR],
            satellite_id=METEOR,
            at_minute=20,
            max_elevation_deg=45,
        )

    report = run_schedule(rollback, SkyfieldOrbitService(), a_request())

    decisions = {row[0]: row[1] for row in _decisions(rollback)}
    assert decisions == {low_early: "scheduled", high: "skipped", low_late: "scheduled"}
    assert report.solver is not None
    assert report.solver.status == "optimal"
    assert report.solver.objective == pytest.approx(660.0)


def test_a_run_is_recorded_and_every_decision_names_it_and_explains_itself(
    rollback: Any, network: dict[str, int]
) -> None:
    with rollback.cursor() as cur:
        winner = _insert_pass(
            cur, network[METEOR], satellite_id=METEOR, at_minute=0, max_elevation_deg=70
        )
        loser = _insert_pass(
            cur,
            network[CUBESAT],
            satellite_id=CUBESAT,
            at_minute=5,
            max_elevation_deg=20,
        )

    report = run_schedule(rollback, SkyfieldOrbitService(), a_request())

    assert report.run_id is not None
    status, source, model, as_of, candidates, scheduled, skipped, config_sha, *_ = (
        _run_record(rollback, report.run_id)
    )
    assert (status, source, model, as_of) == ("optimal", "elevation_proxy", None, None)
    assert (candidates, scheduled, skipped) == (2, 1, 1)
    assert bytes(config_sha) == schedule_config_sha256(ScheduleConfig())
    explained = _explained(rollback)
    assert {row[0] for row in explained.values()} == {report.run_id}
    lost = explained[loser][1]
    assert lost["rule"] == "overlap"
    assert lost["alternative"]["pass_id"] == winner
    assert lost["terms"]["yield_source"] == "elevation_proxy"
    assert lost["terms"]["frames"] == 660.0
    assert lost["run"]["status"] == "optimal"
    kept = explained[winner][1]
    assert (kept["rule"], kept["alternative"]["pass_id"]) == (None, loser)
    assert [row[2] for row in explained.values()] == [None, None]
    assert [row[3] for row in explained.values()] == [None, None]


def test_a_run_with_nothing_to_decide_records_no_run(
    rollback: Any, network: dict[str, int]
) -> None:
    with rollback.cursor() as cur:
        _insert_pass(
            cur, network[METEOR], satellite_id=METEOR, at_minute=0, max_elevation_deg=40
        )
        cur.execute("select count(*) from schedule_runs")
        (before,) = cur.fetchone()

    first = run_schedule(rollback, SkyfieldOrbitService(), a_request())
    again = run_schedule(rollback, SkyfieldOrbitService(), a_request())

    with rollback.cursor() as cur:
        cur.execute("select count(*) from schedule_runs")
        (after,) = cur.fetchone()
    assert first.run_id is not None
    assert (again.run_id, again.solver) == (None, None)
    assert after == before + 1


def test_a_model_s_probability_is_the_yield_and_is_stored_as_predicted(
    rollback: Any, network: dict[str, int]
) -> None:
    """The scorer's probability, not the proxy, and the run names the model."""
    with rollback.cursor() as cur:
        pass_id = _insert_pass(
            cur, network[METEOR], satellite_id=METEOR, at_minute=0, max_elevation_deg=40
        )
    scorer = a_scorer("A")

    report = run_schedule(
        rollback, SkyfieldOrbitService(), a_request("A", "models/a"), scorer
    )

    expected = sigmoid((40.0 - 30.0) / 10.0)
    assert report.run_id is not None
    run = _run_record(rollback, report.run_id)
    assert (run[1], bytes(run[2])) == ("model", scorer.model_sha256)
    run_id, explanation, predicted, model = _explained(rollback)[pass_id]
    assert predicted == pytest.approx(expected)
    assert bytes(model) == scorer.model_sha256
    assert explanation["terms"]["yield_source"] == "model"
    assert explanation["terms"]["yield_path"] == "configured"
    assert explanation["terms"]["value"] == pytest.approx(expected * 660.0)
    assert run_id == report.run_id


def test_a_schedule_that_breaks_a_constraint_is_never_written(
    rollback: Any, network: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-166: the run checks what it is handed, whoever found it.

    The optimiser checks its own answer; this proves the run does not take
    that on trust. It is handed a schedule taking two overlapping passes.
    """
    with rollback.cursor() as cur:
        _insert_pass(
            cur, network[METEOR], satellite_id=METEOR, at_minute=0, max_elevation_deg=70
        )
        _insert_pass(
            cur,
            network[CUBESAT],
            satellite_id=CUBESAT,
            at_minute=5,
            max_elevation_deg=20,
        )

    def everything(scored: Any, **_: Any) -> Optimised:
        return Optimised(
            ScheduleOutcome(selected=list(scored), rejected=[]),
            SolverRun("optimal", "highs", "x", 0.0, 0.0, 0.0, 10.0, None),
        )

    monkeypatch.setattr(run_module, "optimise", everything)

    with pytest.raises(ScheduleInvalidError, match="overlap"):
        run_schedule(rollback, SkyfieldOrbitService(), a_request())
    assert _decisions(rollback) == []


def test_a_run_holds_the_scheduler_s_lock_until_its_transaction_ends(
    rollback: Any, network: dict[str, int], database_url: str
) -> None:
    """Another session cannot take the lock while a run's transaction is open,
    and could before the run began (D-165): two runs never overlap."""
    with rollback.cursor() as cur:
        _insert_pass(
            cur, network[METEOR], satellite_id=METEOR, at_minute=0, max_elevation_deg=40
        )

    def free_elsewhere() -> bool:
        with psycopg.connect(database_url, autocommit=True) as other:
            (taken,) = other.execute(
                "select pg_try_advisory_xact_lock(%s)", (SCHEDULER_LOCK,)
            ).fetchone()
            return bool(taken)

    before = free_elsewhere()
    run_schedule(rollback, SkyfieldOrbitService(), a_request())
    during = free_elsewhere()

    assert (before, during) == (True, False)


# --- reissue (D-171) ------------------------------------------------------------

BEFORE = HORIZON_START - timedelta(hours=1)
"""A round's instant an hour before the horizon, so every window is ahead."""


def _rows_for(conn: Any, pass_id: int) -> list[tuple[Any, ...]]:
    """Every revision of this pass's decisions under A, oldest first."""
    with conn.cursor() as cur:
        cur.execute(
            "select revision, assignment_id, decision, state, revoked_reason,"
            " conflicts_with_assignment_id from assignments"
            " where pass_id = %s and model_config = 'A' order by revision",
            (pass_id,),
        )
        return cur.fetchall()


def _heard(conn: Any, at: datetime) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "update stations set last_heartbeat_at = %s where station_id = %s",
            (at, STATION),
        )


def test_a_skip_decided_again_for_the_same_reason_writes_nothing(
    rollback: Any, network: dict[str, int]
) -> None:
    """The skip is open, and asked again; its answer has not changed."""
    with rollback.cursor() as cur:
        _insert_pass(
            cur, network[METEOR], satellite_id=METEOR, at_minute=0, max_elevation_deg=70
        )
        loser = _insert_pass(
            cur,
            network[CUBESAT],
            satellite_id=CUBESAT,
            at_minute=5,
            max_elevation_deg=20,
        )
        cur.execute("select count(*) from schedule_runs")
        (runs,) = cur.fetchone()

    run_schedule(rollback, SkyfieldOrbitService(), a_request())
    again = run_schedule(rollback, SkyfieldOrbitService(), a_request())

    assert (again.candidates_considered, again.already_decided) == (1, 1)
    assert (again.unchanged, again.rows_written, again.run_id) == (1, 0, None)
    assert len(_rows_for(rollback, loser)) == 1
    with rollback.cursor() as cur:
        cur.execute("select count(*) from schedule_runs")
        assert cur.fetchone() == (runs + 1,)


def test_a_skip_between_two_assignments_is_unchanged_whichever_one_is_named(
    rollback: Any, network: dict[str, int]
) -> None:
    """One high pass overlapping two that do not overlap each other.

    The first round takes the two and names the better of them on the skip.
    The second finds them both as commitments and names the earlier first; the
    one the skip named still blocks it, so nothing new is said, or written.
    """
    with rollback.cursor() as cur:
        earlier = _insert_pass(
            cur, network[METEOR], satellite_id=METEOR, at_minute=0, max_elevation_deg=40
        )
        better = _insert_pass(
            cur,
            network[METEOR],
            satellite_id=METEOR,
            at_minute=16,
            max_elevation_deg=60,
        )
        between = _insert_pass(
            cur,
            network[CUBESAT],
            satellite_id=CUBESAT,
            at_minute=8,
            max_elevation_deg=80,
        )

    run_schedule(rollback, SkyfieldOrbitService(), a_request(now=BEFORE))
    again = run_schedule(rollback, SkyfieldOrbitService(), a_request(now=BEFORE))

    decisions = {row[0]: (row[1], row[3]) for row in _decisions(rollback)}
    assert decisions[between] == ("skipped", assignment_id_for(better, "A"))
    assert decisions[earlier][0] == decisions[better][0] == "scheduled"
    assert (again.unchanged, again.rows_written, again.run_id) == (1, 0, None)
    assert len(_rows_for(rollback, between)) == 1


def test_a_declined_pass_frees_its_slot_for_the_pass_it_displaced(
    rollback: Any, network: dict[str, int]
) -> None:
    """Decline, then reissue to the freed slot (D-171).

    The declined pass is not offered again — the station just let it go — and
    the skip that lost to it is decided again, taken, as revision 1.
    """
    with rollback.cursor() as cur:
        winner = _insert_pass(
            cur, network[METEOR], satellite_id=METEOR, at_minute=0, max_elevation_deg=70
        )
        loser = _insert_pass(
            cur,
            network[CUBESAT],
            satellite_id=CUBESAT,
            at_minute=5,
            max_elevation_deg=20,
        )
    run_schedule(rollback, SkyfieldOrbitService(), a_request(now=BEFORE))
    with rollback.cursor() as cur:
        cur.execute(
            "update assignments set state = 'held' where assignment_id = %s",
            (assignment_id_for(winner, "A"),),
        )
    assert revoke_declined(rollback, STATION, still_held=[], now=BEFORE) == 1

    again = run_schedule(rollback, SkyfieldOrbitService(), a_request(now=BEFORE))

    assert [row[2:5] for row in _rows_for(rollback, winner)] == [
        ("scheduled", "revoked", "declined")
    ]
    revisions = _rows_for(rollback, loser)
    assert [(row[0], row[2], row[3]) for row in revisions] == [
        (0, "skipped", "issued"),
        (1, "scheduled", "issued"),
    ]
    assert revisions[1][1] == assignment_id_for(loser, "A", 1)
    assert (again.scheduled, again.rows_written) == (1, 1)


def test_an_offline_station_s_work_is_revoked_and_decided_again_on_return(
    rollback: Any, network: dict[str, int]
) -> None:
    """Offline, revoked; back without naming it, decided again as revision 1."""
    with rollback.cursor() as cur:
        pass_id = _insert_pass(
            cur, network[METEOR], satellite_id=METEOR, at_minute=0, max_elevation_deg=40
        )
    _heard(rollback, BEFORE)
    run_schedule(rollback, SkyfieldOrbitService(), a_request(now=BEFORE))

    _heard(rollback, BEFORE - timedelta(minutes=10))
    away = run_schedule(rollback, SkyfieldOrbitService(), a_request(now=BEFORE))

    assert away.revoked == 1
    assert _rows_for(rollback, pass_id)[0][3:5] == ("revoked", "offline")

    _heard(rollback, BEFORE)
    back = run_schedule(rollback, SkyfieldOrbitService(), a_request(now=BEFORE))

    rows = _rows_for(rollback, pass_id)
    assert [(row[0], row[2], row[3]) for row in rows] == [
        (0, "scheduled", "revoked"),
        (1, "scheduled", "issued"),
    ]
    assert back.rows_written == 1
    listed = find_assignments(
        rollback,
        not_before=HORIZON_START - timedelta(days=1),
        station_id=STATION,
        decision=None,
        after_assignment_id=None,
        limit=10,
    )
    assert [(one.pass_id, one.revision) for one in listed] == [(pass_id, 1)]


# --- the declared horizon (D-175) ---------------------------------------------


def _declare(conn: Any, floor_deg: float) -> None:
    """Give the station's chain a mask that holds ``floor_deg`` all the way round.

    90° blocks every pass whatever its geometry, since no sample is above it,
    and -90° blocks none. So these tests do not depend on where a pass inserted
    at a chosen time happens to put the satellite.
    """
    with conn.cursor() as cur:
        cur.execute(
            "update station_capabilities set horizon_mask_json = %s::jsonb"
            " where station_id = %s",
            (f'[{{"az_deg": 0, "min_el_deg": {floor_deg}}}]', STATION),
        )


def test_a_pass_behind_the_declared_horizon_is_left_undecided(
    rollback: Any, network: dict[str, int]
) -> None:
    """Not a candidate, and not a skip: a skip is final, and masks change."""
    with rollback.cursor() as cur:
        blocked = _insert_pass(
            cur, network[METEOR], satellite_id=METEOR, at_minute=0, max_elevation_deg=40
        )
    _declare(rollback, 90.0)

    report = run_schedule(rollback, SkyfieldOrbitService(), a_request())

    assert report.passes_below_the_declared_horizon == (blocked,)
    assert report.candidates_considered == 0
    assert _decisions(rollback) == []


def test_a_pass_over_the_declared_horizon_is_scheduled(
    rollback: Any, network: dict[str, int]
) -> None:
    """The positive control: the same pass, a mask it clears."""
    with rollback.cursor() as cur:
        _insert_pass(
            cur, network[METEOR], satellite_id=METEOR, at_minute=0, max_elevation_deg=40
        )
    _declare(rollback, -90.0)

    report = run_schedule(rollback, SkyfieldOrbitService(), a_request())

    assert report.passes_below_the_declared_horizon == ()
    assert [row[1] for row in _decisions(rollback)] == ["scheduled"]


def test_a_corrected_mask_gives_the_pass_back(
    rollback: Any, network: dict[str, int]
) -> None:
    """Left undecided, the pass is decided by the first run after the fix."""
    with rollback.cursor() as cur:
        _insert_pass(
            cur, network[METEOR], satellite_id=METEOR, at_minute=0, max_elevation_deg=40
        )
    _declare(rollback, 90.0)
    run_schedule(rollback, SkyfieldOrbitService(), a_request())

    _declare(rollback, -90.0)
    report = run_schedule(rollback, SkyfieldOrbitService(), a_request())

    assert report.scheduled == 1
    assert [row[1] for row in _decisions(rollback)] == ["scheduled"]


def test_a_mask_on_a_chain_that_cannot_receive_the_pass_is_ignored(
    rollback: Any, network: dict[str, int]
) -> None:
    """Only a chain that could receive the downlink can be in its way."""
    with rollback.cursor() as cur:
        _insert_pass(
            cur, network[METEOR], satellite_id=METEOR, at_minute=0, max_elevation_deg=40
        )
        cur.execute(
            "insert into station_capabilities (station_id, band, freq_min_hz,"
            " freq_max_hz, modes, polarisation, min_elevation_deg,"
            " horizon_mask_json)"
            " values (%s, 'uhf', 435000000, 438000000, '{fsk}', 'rhcp', 10,"
            ' \'[{"az_deg": 0, "min_el_deg": 90}]\'::jsonb)',
            (STATION,),
        )

    report = run_schedule(rollback, SkyfieldOrbitService(), a_request())

    assert report.passes_below_the_declared_horizon == ()
    assert report.scheduled == 1
