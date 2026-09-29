"""Stage 18's gate, the database half: every stored decision records its reasoning.

``tests/unit/test_scheduler_gate.py`` asserts the gate through the commands
that need no database. What only a database can show is here: that a round —
``run_schedule``, which ``meridian schedule`` and the jobs service both call —
leaves every decision it writes naming a recorded run and carrying its
explanation, under every configuration, with the solver working or failing,
and that what it writes never puts one antenna on two passes.

The checks are queries over what was stored, not over what the run returned:
the claim is about the record a reader will find. Each has its positive
control: a decision written without a run is found by the first, and two
overlapping assignments by the second.

Reference: docs/DECISIONS.md D-165 to D-170.
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from meridian.orbit.skyfield_service import SkyfieldOrbitService  # noqa: E402
from meridian.prediction.live import LiveScorer  # noqa: E402
from meridian.prediction.score import Linear, Model  # noqa: E402
from meridian.scheduler import optimiser as optimiser_module  # noqa: E402
from meridian.scheduler.programme import Answer  # noqa: E402
from meridian.scheduler.run import ScheduleRequest, run_schedule  # noqa: E402
from meridian.scheduler.schedule_config import ScheduleConfig  # noqa: E402
from meridian.store.schedule_writes import (  # noqa: E402
    NewAssignment,
    insert_assignments,
)

pytestmark = pytest.mark.integration

STATION = "st_gate"
SATELLITES = {"norad:57166": 1.0, "norad:99124": 2.0}
HORIZON_START = datetime(2026, 8, 20, tzinfo=UTC)
HORIZON_END = HORIZON_START + timedelta(days=1)
EPOCH = HORIZON_START - timedelta(days=1)
LINE1 = "1 57166U 23091A   26231.00000000  .00000098  00000-0  61234-4 0  9995"
LINE2 = "2 57166  98.7123 201.3345 0002145  85.1234 275.0123 14.22150000123456"


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    """Undo everything this test writes — see test_store_invites.py's twin."""
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def busy(rollback: Any) -> list[int]:
    """One station and thirty seeded passes over a day, many overlapping."""
    rng = random.Random(18)
    with rollback.cursor() as cur:
        cur.execute(
            "insert into stations (station_id, name, operator, lat_deg, lon_deg,"
            " alt_m, token_sha256, registration_key_sha256)"
            " values (%s, 'G', 'tests', 17.4, 78.5, 542, %s, %s)",
            (STATION, bytes([5]) * 32, bytes([6]) * 32),
        )
        cur.execute(
            "insert into station_capabilities (station_id, band, freq_min_hz,"
            " freq_max_hz, modes, polarisation, min_elevation_deg)"
            " values (%s, 'vhf', 136000000, 138000000, '{lrpt}', 'rhcp', 10)",
            (STATION,),
        )
        sets = {}
        for index, (satellite, priority) in enumerate(SATELLITES.items()):
            cur.execute(
                "insert into satellites (satellite_id, name, priority)"
                " values (%s, %s, %s)",
                (satellite, satellite, priority),
            )
            cur.execute(
                "insert into satellite_transmitters (satellite_id, centre_freq_hz,"
                " mode) values (%s, %s, 'lrpt')",
                (satellite, 137_100_000 + 500_000 * index),
            )
            cur.execute(
                "insert into element_sets (satellite_id, epoch, line1, line2, source)"
                " values (%s, %s, %s, %s, 'manual') returning id",
                (satellite, EPOCH, LINE1, LINE2.replace("57166", satellite[-5:])),
            )
            sets[satellite] = cur.fetchone()[0]
        pass_ids = []
        for _ in range(30):
            satellite = rng.choice(list(SATELLITES))
            aos = HORIZON_START + timedelta(minutes=rng.uniform(0, 1400))
            cur.execute(
                "insert into passes (satellite_id, station_id, aos, los,"
                " max_elevation_deg, max_elevation_at, aos_azimuth_deg,"
                " los_azimuth_deg, element_set_id, min_elevation_deg, simulated)"
                " values (%s, %s, %s, %s, %s, %s, 10, 200, %s, 10, false)"
                " returning id",
                (
                    satellite,
                    STATION,
                    aos,
                    aos + timedelta(minutes=rng.uniform(8, 15)),
                    rng.uniform(10, 85),
                    aos + timedelta(minutes=5),
                    sets[satellite],
                ),
            )
            pass_ids.append(cur.fetchone()[0])
    return pass_ids


def a_scorer(configuration: str) -> LiveScorer:
    """A model of peak elevation alone, written by hand, under ``configuration``."""
    linear = Linear(
        features=("max_elevation_deg",),
        mean=(40.0,),
        scale=(15.0,),
        coefficients=(1.2,),
        intercept=-0.3,
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


def schedule(conn: Any, configuration: str) -> Any:
    """One round under ``configuration``, a model's for C and D."""
    learned = configuration in ("C", "D")
    config = ScheduleConfig(
        configuration=configuration,
        model=f"models/{configuration.lower()}" if learned else None,
    )
    request = ScheduleRequest(
        start=HORIZON_START,
        end=HORIZON_END,
        now=HORIZON_START - timedelta(hours=1),
        config=config,
    )
    scorer = a_scorer(configuration) if learned else None
    return run_schedule(conn, SkyfieldOrbitService(), request, scorer)


def unexplained(conn: Any) -> list[str]:
    """Every stored decision that does not record its reasoning, and why not.

    A decision records it when it names a run of its own configuration, the
    run's status is the one its explanation states, its explanation's value
    is the score it was decided with, a skip's alternative is the pass whose
    assignment it names, and a model's decision names the model and its
    probability.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            select a.assignment_id, a.decision, a.score, a.explanation,
                   a.model_config, a.model_sha256, a.predicted_yield,
                   r.model_config, r.status, r.model_sha256, blocker.pass_id
            from assignments a
            left join schedule_runs r on r.run_id = a.schedule_run_id
            left join assignments blocker
              on blocker.assignment_id = a.conflicts_with_assignment_id
            where a.station_id = %s
            """,
            (STATION,),
        )
        rows = cur.fetchall()
    found = []
    for (
        assignment_id,
        decision,
        score,
        explanation,
        config,
        model,
        predicted,
        run_config,
        run_status,
        run_model,
        blocker_pass,
    ) in rows:
        if run_config is None or explanation is None:
            found.append(f"{assignment_id}: no run or no explanation")
            continue
        checks = {
            "run of another configuration": run_config == config,
            "run status not stated": explanation["run"]["status"] == run_status,
            "value is not the score": explanation["terms"]["value"]
            == pytest.approx(score),
            "model not named": model == run_model,
            "no probability from the model": (predicted is not None)
            == (run_model is not None),
            "alternative is not the blocker": decision != "skipped"
            or blocker_pass is None
            or explanation["alternative"]["pass_id"] == blocker_pass,
        }
        found.extend(f"{assignment_id}: {why}" for why, ok in checks.items() if not ok)
    return found


def overlapping(conn: Any) -> list[tuple[str, str]]:
    """Pairs of the station's live assignments whose windows overlap."""
    with conn.cursor() as cur:
        cur.execute(
            """
            select a.assignment_id, b.assignment_id
            from assignments a join assignments b
              on a.station_id = b.station_id and a.assignment_id < b.assignment_id
            where a.station_id = %s
              and a.decision = 'scheduled' and b.decision = 'scheduled'
              and a.state <> 'revoked' and b.state <> 'revoked'
              and a.start_at < b.end_at and b.start_at < a.end_at
            """,
            (STATION,),
        )
        return [tuple(row) for row in cur.fetchall()]


def test_every_decision_of_every_configuration_records_its_reasoning(
    rollback: Any, busy: list[int]
) -> None:
    """A to D over one busy day: every pass decided four times, each decision
    explained, and no two assignments on one antenna at once."""
    reports = [schedule(rollback, name) for name in ("A", "B", "C", "D")]

    with rollback.cursor() as cur:
        cur.execute(
            "select model_config, count(*) from assignments where station_id = %s"
            " group by model_config order by model_config",
            (STATION,),
        )
        counts = cur.fetchall()
    assert counts == [(name, len(busy)) for name in ("A", "B", "C", "D")]
    assert all(one.solver is not None for one in reports)
    assert {one.solver.status for one in reports} == {"optimal"}
    assert sum(one.skipped for one in reports) > 0
    assert unexplained(rollback) == []
    assert overlapping(rollback) == []


@pytest.mark.usefixtures("busy")
def test_a_solver_with_no_answer_still_writes_a_valid_explained_schedule(
    rollback: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_answer(*_: object) -> Answer:
        return Answer(None, "fallback", None, 0.0, "the gate took the answer away")

    monkeypatch.setattr(optimiser_module, "solve", no_answer)

    report = schedule(rollback, "D")

    assert report.solver is not None
    assert (report.solver.status, report.solver.detail) == (
        "fallback",
        "the gate took the answer away",
    )
    assert report.scheduled > 0
    assert unexplained(rollback) == []
    assert overlapping(rollback) == []


def test_the_checks_notice_a_decision_without_reasoning_and_an_overlap(
    rollback: Any, busy: list[int]
) -> None:
    """Positive control: both queries can fail. Two scheduled decisions are
    written directly, with no run and no explanation, over one window."""
    with rollback.cursor() as cur:
        cur.execute("select id, aos, los from passes where id = any(%s)", (busy[:2],))
        first, second = cur.fetchall()
    rows = [
        NewAssignment(
            assignment_id=f"as_gate_{pass_id}",
            pass_id=pass_id,
            station_id=STATION,
            start_at=first[1],
            end_at=first[2],
            centre_freq_hz=137_100_000,
            mode="lrpt",
            timing_uncertainty_s=0.5,
            decision="scheduled",
            reason="written by the gate's control",
            model_config="A",
            score=1.0,
            conflicts_with_assignment_id=None,
            priority=1.0,
            simulated=False,
        )
        for pass_id in (first[0], second[0])
    ]

    insert_assignments(rollback, rows)

    assert sorted(unexplained(rollback)) == [
        f"as_gate_{first[0]}: no run or no explanation",
        f"as_gate_{second[0]}: no run or no explanation",
    ]
    pair = tuple(sorted(one.assignment_id for one in rows))
    assert overlapping(rollback) == [pair]
