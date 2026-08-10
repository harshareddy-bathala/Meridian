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
from meridian.scheduler.assignment_records import assignment_id_for  # noqa: E402
from meridian.scheduler.run import ScheduleRequest, run_schedule  # noqa: E402

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


def a_request(model_config: str = "A") -> ScheduleRequest:
    """One run over the whole horizon, for a station that does not slew."""
    return ScheduleRequest(
        start=HORIZON_START,
        end=HORIZON_END,
        model_config=model_config,
        turnaround_s=0.0,
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
    assert second.scheduled == 1
    assert second.rows_written == 0
    assert len(_decisions(rollback)) == 1


def test_two_configurations_coexist_over_one_horizon(
    rollback: Any, network: dict[str, int]
) -> None:
    """The ablation in EVALUATION.md section 3 needs both schedules at once.

    Keyed on the pass alone the second run would have written nothing and the
    comparison would have had one side.
    """
    with rollback.cursor() as cur:
        _insert_pass(
            cur, network[METEOR], satellite_id=METEOR, at_minute=0, max_elevation_deg=40
        )

    run_schedule(rollback, SkyfieldOrbitService(), a_request("A"))
    run_schedule(rollback, SkyfieldOrbitService(), a_request("B"))

    assert len(_decisions(rollback, "A")) == 1
    assert len(_decisions(rollback, "B")) == 1


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

    run_schedule(rollback, SkyfieldOrbitService(), a_request("A"))
    run_schedule(rollback, SkyfieldOrbitService(), a_request("B"))

    taken_by_a = [row[0] for row in _decisions(rollback, "A") if row[1] == "scheduled"]
    taken_by_b = [row[0] for row in _decisions(rollback, "B") if row[1] == "scheduled"]

    assert taken_by_a == [higher]
    assert taken_by_b == [weighted]


def test_the_score_stored_is_the_one_the_decision_was_made_with(
    rollback: Any, network: dict[str, int]
) -> None:
    """Configuration A stores degrees; B stores degrees times the weighting."""
    with rollback.cursor() as cur:
        cur.execute(
            "update satellites set priority = 2.0 where satellite_id = %s", (METEOR,)
        )
        _insert_pass(
            cur, network[METEOR], satellite_id=METEOR, at_minute=0, max_elevation_deg=45
        )

    run_schedule(rollback, SkyfieldOrbitService(), a_request("A"))
    run_schedule(rollback, SkyfieldOrbitService(), a_request("B"))

    assert _decisions(rollback, "A")[0][2] == 45.0
    assert _decisions(rollback, "B")[0][2] == 90.0


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


def test_a_configuration_this_stage_does_not_implement_is_refused(
    rollback: Any, network: dict[str, int]
) -> None:
    """Naming D today must fail loudly rather than fall back to A.

    A silent fallback would publish a number under a label it did not earn, and
    the ablation is exactly a comparison between labels.
    """
    assert network
    with pytest.raises(ValueError, match="configuration"):
        run_schedule(rollback, SkyfieldOrbitService(), a_request("D"))


def test_a_naive_horizon_is_refused(rollback: Any, network: dict[str, int]) -> None:
    """CLAUDE.local.md section 6: a naive datetime is a bug, not a tolerance."""
    assert network
    naive = ScheduleRequest(
        start=HORIZON_START.replace(tzinfo=None),
        end=HORIZON_END,
        model_config="A",
        turnaround_s=0.0,
    )
    with pytest.raises(ValueError, match="naive"):
        run_schedule(rollback, SkyfieldOrbitService(), naive)
