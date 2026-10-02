"""A diagnosis run end to end, on a planted world, against the real database.

Marked ``integration`` by the directory hook. Each loss is planted with the
evidence one cause leaves, and the run is read back from ``loss_diagnoses``:

* a floor raised against the station's own → *interference*;
* heartbeats stamped twenty minutes off → *timing fault*;
* a window held with nothing reported, listening not confirmed → *station not
  listening*;
* a silence while another station heard the satellite → *undetermined*;
* a decode below the verdict's partial threshold is diagnosed, one above it is
  not, and neither is an expired or a revoked assignment (D-272);
* **simulated evidence never reaches a measured station's diagnosis**, nor the
  other way round: two simulated stations hearing nothing beside a measured one
  that heard the satellite name it silent for themselves only;
* a re-run writes nothing, and changed thresholds diagnose again beside;
* **nothing reaches for a network** while it runs, with the real orbit service
  placing every sample.

Reference: docs/DECISIONS.md D-102, D-105, D-272 to D-277.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

pytest.importorskip("psycopg")

from meridian.orbit.skyfield_service import SkyfieldOrbitService
from meridian.registry import ListeningQuery
from meridian.reliability.classification import METHOD as CLASSIFIED
from meridian.reliability.config import (
    DiagnosisConfig,
    ReliabilityConfig,
)
from meridian.reliability.diagnosis_run import diagnose_settled
from meridian.store.noise_measurements import record_observation_floor

pytestmark = pytest.mark.integration

T = datetime(2026, 8, 14, 9, 0, tzinfo=UTC)
CONFIG = ReliabilityConfig()
VERDICT = "verdict-1:0123456789ab"
HOUR = timedelta(hours=1)


class Listening:
    """Confirms every station was listening, and counts the questions."""

    def __init__(self) -> None:
        self.asked: list[ListeningQuery] = []

    def was_listening(self, query: ListeningQuery) -> bool:
        self.asked.append(query)
        return True


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    with conn.transaction(force_rollback=True):
        yield conn


def samples(start: datetime, peak: float = 12.0) -> str:
    """Twenty-five SNR samples over eleven minutes, rising and falling."""
    step = timedelta(minutes=11) / 24
    values = [round(peak * (1 - abs(i - 12) / 12) - 1.0, 1) for i in range(25)]
    return json.dumps(
        [
            {"t": (start + step * i).isoformat().replace("+00:00", "Z"), "snr_db": v}
            for i, v in enumerate(values)
        ]
    )


class World:
    """Rows planted for one run, through the shared fixture's helpers."""

    def __init__(self, conn: Any, rows: Any) -> None:
        self.conn = conn
        self.rows = rows
        self.element_set = rows.satellite()

    def loss(  # noqa: PLR0913 — one planted reception, each property by name
        self,
        name: str,
        station: str,
        at: datetime,
        *,
        outcome: str | None,
        classification: str | None,
        state: str = "reported",
        floor: float = -60.0,
    ) -> None:
        pass_id = self.rows.pass_(station, at, element_set_id=self.element_set)
        self.rows.assignment(name, pass_id, state=state)
        if outcome is not None:
            self.rows.observation(name, outcome=outcome)
            self.conn.execute(
                "update observations set noise_floor_dbfs = %s,"
                " receiver_gain_db = 30.0, snr_samples = %s"
                " where assignment_id = %s",
                (floor, samples(at, 12.0 if outcome != "no_signal" else 0.5), name),
            )
            record_observation_floor(self.conn, assignment_id=name, revision=1)
        if classification is not None:
            confirmed = classification != "station_not_confirmed_listening"
            self.conn.execute(
                "insert into pass_classifications (assignment_id, assignment_ids,"
                " pass_id, station_id, satellite_id, window_start, window_end,"
                " classification, evidence, method, config_sha256, simulated)"
                " select a.assignment_id, array[a.assignment_id], a.pass_id,"
                " a.station_id, p.satellite_id, a.start_at, a.end_at, %s, %s, %s,"
                " %s, a.simulated from assignments a join passes p on p.id = a.pass_id"
                " where a.assignment_id = %s",
                (
                    classification,
                    json.dumps(
                        {"heard_during_window": True, "listening_confirmed": confirmed}
                    ),
                    CLASSIFIED,
                    CONFIG.classification.sha256(),
                    name,
                ),
            )


@pytest.fixture
def world(rollback: Any, schedule_rows: Any) -> Any:
    planted = World(rollback, schedule_rows)
    for name, simulated in (
        ("st_diag", False),
        ("st_diag_other", False),
        ("st_diag_sim", True),
        ("st_diag_sim_other", True),
    ):
        schedule_rows.station(name, simulated=simulated)
    # A week of the measured station's own receptions: its baseline and history.
    for day in range(1, 7):
        planted.loss(
            f"as_history_{day}",
            "st_diag",
            T - timedelta(days=day),
            outcome="decoded",
            classification="successful_reception",
        )
    # One pass, four stations: one measured station heard it, the simulated two
    # did not, and the measured one being diagnosed did not either.
    planted.loss(
        "as_quiet", "st_diag", T, outcome="no_signal", classification="confirmed_miss"
    )
    planted.loss(
        "as_heard_elsewhere",
        "st_diag_other",
        T,
        outcome="decoded",
        classification="successful_reception",
    )
    planted.loss(
        "as_sim_quiet",
        "st_diag_sim",
        T,
        outcome="no_signal",
        classification="confirmed_miss",
    )
    planted.loss(
        "as_sim_other",
        "st_diag_sim_other",
        T,
        outcome="no_signal",
        classification="confirmed_miss",
    )
    planted.loss(
        "as_noisy",
        "st_diag",
        T + 2 * HOUR,
        outcome="signal_no_decode",
        classification="signal_no_decode",
        floor=-54.0,
    )
    planted.loss(
        "as_skewed",
        "st_diag",
        T + 4 * HOUR,
        outcome="no_signal",
        classification="confirmed_miss",
    )
    planted.loss(
        "as_empty",
        "st_diag",
        T + 6 * HOUR,
        outcome=None,
        classification="station_not_confirmed_listening",
        state="held",
    )
    planted.loss(
        "as_partial",
        "st_diag",
        T + 8 * HOUR,
        outcome="decoded",
        classification="successful_reception",
    )
    planted.loss(
        "as_good",
        "st_diag",
        T + 10 * HOUR,
        outcome="decoded",
        classification="successful_reception",
    )
    planted.loss(
        "as_expired",
        "st_diag",
        T + 12 * HOUR,
        outcome=None,
        classification="assignment_declined",
        state="expired",
    )
    for minute in range(0, 12, 2):
        received = T + 4 * HOUR + timedelta(minutes=minute)
        rollback.execute(
            "insert into heartbeats (station_id, sent_at, received_at, state)"
            " values ('st_diag', %s, %s, 'idle')",
            (received + timedelta(minutes=20), received),
        )
    for name, probability in (("as_partial", 0.2), ("as_good", 0.9)):
        rollback.execute(
            "insert into reception_verdicts (assignment_id, revision,"
            " observation_started_at, station_id, probability_usable, method, route,"
            " inputs_sha256, partial_below, simulated)"
            " select assignment_id, revision, started_at, station_id, %s, %s,"
            " 'full', %s, 0.4, simulated from observations where assignment_id = %s",
            (probability, VERDICT, bytes(32), name),
        )
    return rollback


def diagnosed(conn: Any) -> dict[str, tuple[str, list[dict[str, Any]], dict[str, Any]]]:
    rows = conn.execute(
        "select assignment_id, cause, candidates_json, evidence_json"
        " from loss_diagnoses"
    ).fetchall()
    return {row[0]: (row[1], row[2], row[3]) for row in rows}


def candidate(found: list[dict[str, Any]], cause: str) -> dict[str, Any]:
    (one,) = (one for one in found if one["cause"] == cause)
    return one


def run(conn: Any, config: ReliabilityConfig = CONFIG) -> Any:
    return diagnose_settled(
        conn,
        Listening(),
        SkyfieldOrbitService(),
        config=config,
        verdict_method=VERDICT,
    )


def test_each_planted_loss_is_named_for_what_it_left(
    world: Any, network_guard: Any
) -> None:
    network_guard(allow=("psycopg",))

    report = run(world)
    found = diagnosed(world)

    assert report.diagnosed == report.written == len(found) == 7
    assert {name: cause for name, (cause, _, _) in found.items()} == {
        "as_quiet": "undetermined",
        "as_sim_quiet": "satellite_silent",
        "as_sim_other": "satellite_silent",
        "as_noisy": "interference",
        "as_skewed": "timing_fault",
        "as_empty": "station_not_listening",
        "as_partial": "undetermined",
    }


def test_simulated_evidence_never_reaches_a_measured_station(world: Any) -> None:
    run(world)
    found = diagnosed(world)

    measured = candidate(found["as_quiet"][1], "satellite_silent")["found"]
    simulated = candidate(found["as_sim_quiet"][1], "satellite_silent")["found"]
    # The measured station counts the measured one that heard it, and none of
    # the simulated silences; the simulated stations count each other only.
    assert (measured["signals"], measured["silences"]) == (1, 0)
    assert (simulated["signals"], simulated["silences"]) == (0, 1)


def test_a_partial_decode_names_the_verdict_it_was_read_against(world: Any) -> None:
    run(world)
    found = diagnosed(world)

    assert found["as_partial"][2]["loss"] == "partial"
    assert found["as_partial"][2]["verdict"] == {
        "method": VERDICT,
        "probability_usable": 0.2,
        "partial_below": 0.4,
    }
    assert found["as_empty"][2]["loss"] == "empty"
    assert "as_good" not in found
    assert "as_expired" not in found


def test_every_sample_is_placed_and_every_cause_recorded(world: Any) -> None:
    run(world)
    cause, candidates, evidence = diagnosed(world)["as_noisy"]

    assert cause == "interference"
    assert [one["cause"] for one in candidates] == [
        "satellite_silent",
        "station_not_listening",
        "obstruction",
        "interference",
        "timing_fault",
    ]
    assert candidate(candidates, "obstruction")["found"]["samples"] == 25
    assert candidate(candidates, "obstruction")["found"]["reason"] == "floor raised"
    assert candidate(candidates, "interference")["found"]["lift_db"] == 6.0
    assert evidence["parameters"] == DiagnosisConfig().parameters()


def test_a_rerun_writes_nothing_and_new_thresholds_write_beside(world: Any) -> None:
    first = run(world)
    again = run(world)
    changed = run(
        world, ReliabilityConfig(diagnosis=DiagnosisConfig(interference_lift_db=3.0))
    )

    assert first.written == 7
    assert (again.diagnosed, again.written) == (0, 0)
    assert changed.written == 7
    count = world.execute("select count(*) from loss_diagnoses").fetchone()[0]
    assert count == 14


def test_a_bounded_run_takes_the_oldest_and_says_more_remain(world: Any) -> None:
    report = diagnose_settled(
        world,
        Listening(),
        SkyfieldOrbitService(),
        config=CONFIG,
        verdict_method=VERDICT,
        limit=2,
    )

    assert report.diagnosed == 2
    assert report.deferred
