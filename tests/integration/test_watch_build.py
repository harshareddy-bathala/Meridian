"""Training baselines — which receptions a chain learns from, and when it trains.

Marked ``integration`` by the directory hook. The orbit service is a stand-in
whose every pass climbs to 58° and back, so each sample's elevation is known
exactly; what is under test is what the store reads and what is written:
- a chain is trained from its own heard receptions, once a day;
- a loss Stage 27 put down to another cause is not learned;
- a reception two capabilities cover belongs to neither;
- a baseline is not trained again while a warning is open;
- MSP 0.2's peaks make a baseline of their own.

Reference: docs/DECISIONS.md D-290, D-291, D-293.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from meridian.orbit.types import LookAngle  # noqa: E402
from meridian.reliability import diagnosis  # noqa: E402
from meridian.reliability.config import BaselineConfig, ReliabilityConfig  # noqa: E402
from meridian.reliability.watch_build import (  # noqa: E402
    baseline_sha256,
    build_baselines,
)
from meridian.store.receive_chain_warnings import (  # noqa: E402
    NewWarning,
    raise_warning,
)
from meridian.store.signal_baselines import (  # noqa: E402
    NewBaseline,
    find_current_baseline,
    insert_baseline,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
TODAY = datetime(2026, 10, 20, tzinfo=UTC)
FIRST = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
WINDOW = timedelta(minutes=10)
STEP_S = 5
PEAK_DEG = 58.0
CONFIG = ReliabilityConfig()


class Overhead:
    """Every pass climbs to :data:`PEAK_DEG` at its middle and sets again."""

    def look_angles(
        self,
        _element_set: Any,
        _site: Any,
        start: datetime,
        end: datetime,
        *,
        step_s: float,
    ) -> list[LookAngle]:
        count = int((end - start).total_seconds() // step_s)
        return [
            LookAngle(
                t=start + timedelta(seconds=k * step_s),
                azimuth_deg=90.0,
                elevation_deg=elevation(k, count),
                range_km=900.0,
                range_rate_km_s=0.0,
            )
            for k in range(count)
        ]


def elevation(k: int, count: int) -> float:
    return PEAK_DEG * math.sin(math.pi * k / max(count - 1, 1))


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    with conn.transaction(force_rollback=True):
        yield conn


def capability(conn: Any, station_id: str, low: int = 137_000_000) -> int:
    return int(
        conn.execute(
            "insert into station_capabilities (station_id, band, freq_min_hz,"
            " freq_max_hz, modes, polarisation, min_elevation_deg)"
            " values (%s, 'vhf', %s, 138000000, '{lrpt}', 'rhcp', 5) returning id",
            (station_id, low),
        ).fetchone()[0]
    )


def reception(
    rows: Any, station: str, number: int, *, loss_db: float = 0.0, samples: bool = True
) -> str:
    """One decoded reception, its SNR a quarter of its elevation less ``loss_db``."""
    element_set = rows.conn.execute(
        "select id from element_sets where satellite_id = 'norad:99970'"
    ).fetchone()
    element_set = element_set[0] if element_set else rows.satellite()
    aos = FIRST + timedelta(hours=12 * number)
    pass_id = rows.pass_(station, aos, element_set_id=element_set, minutes=10)
    assignment = rows.assignment(f"{station}-r{number}", pass_id, state="reported")
    rows.observation(assignment)
    if samples:
        count = int(WINDOW.total_seconds() // STEP_S) + 1
        series = [
            {
                "t": (aos + timedelta(seconds=k * STEP_S)).isoformat(),
                "snr_db": round(elevation(k, count + 1) / 4 - loss_db, 2),
            }
            for k in range(count)
        ]
        rows.conn.execute(
            "update observations set snr_samples = %s where assignment_id = %s",
            (json.dumps(series), assignment),
        )
    return assignment


def diagnose_as(conn: Any, assignment_id: str, cause: str) -> None:
    """A Stage 27 diagnosis of the reception, under the deployment's diagnosis."""
    classification = conn.execute(
        "insert into pass_classifications (assignment_id, assignment_ids, pass_id,"
        " station_id, satellite_id, window_start, window_end, classification,"
        " evidence, method, config_sha256, simulated)"
        " select a.assignment_id, array[a.assignment_id], a.pass_id, a.station_id,"
        " p.satellite_id, a.start_at, a.end_at, 'successful_reception', '{}',"
        " 'classification-1', %s, a.simulated"
        " from assignments a join passes p on p.id = a.pass_id"
        " where a.assignment_id = %s returning classification_id",
        (bytes(32), assignment_id),
    ).fetchone()[0]
    conn.execute(
        "insert into loss_diagnoses (assignment_id, revision, observation_started_at,"
        " station_id, classification_id, cause, candidates_json, evidence_json,"
        " method, config_sha256, simulated)"
        " select o.assignment_id, o.revision, o.started_at, o.station_id, %s, %s,"
        " '[]', '{}', %s, %s, o.simulated from observations o"
        " where o.assignment_id = %s",
        (
            classification,
            cause,
            diagnosis.METHOD,
            CONFIG.diagnosis.sha256(),
            assignment_id,
        ),
    )


def build(conn: Any, config: ReliabilityConfig = CONFIG, **kw: Any) -> Any:
    return build_baselines(conn, Overhead(), config=config, now=NOW, **kw)  # type: ignore[arg-type]


def current(conn: Any, station: str, chain: int, evidence: str = "samples") -> Any:
    return find_current_baseline(
        conn,
        station_id=station,
        capability_id=chain,
        evidence=evidence,
        method="baseline-1",
        config_sha256=baseline_sha256(CONFIG),
    )


def test_a_chain_is_trained_from_its_own_receptions_once_a_day(
    rollback: Any, schedule_rows: Any
) -> None:
    station = schedule_rows.station("st-train", simulated=False)
    chain = capability(rollback, station)
    for number in range(6):
        reception(schedule_rows, station, number)

    first = build(rollback, station_id=station)
    again = build(rollback, station_id=station)

    assert (first.written, first.chains, first.simulated) == (1, 1, 0)
    assert (again.written, again.kept) == (0, 1)
    found = current(rollback, station, chain)
    assert found is not None
    assert (found.trained_from, found.trained_until) == (TODAY - timedelta(14), TODAY)
    assert found.receptions == 6
    assert found.simulated is False
    edges = [one["elevation_deg"] for one in found.bins_json]
    assert edges == [0.0, 10.0, 20.0, 30.0, 40.0, 50.0]
    # A quarter of the elevation: the 50-58° bin sits near 54°/4.
    assert 12.5 <= found.bins_json[-1]["median_db"] <= 15.0


def test_a_loss_put_down_to_another_cause_is_not_learned(
    rollback: Any, schedule_rows: Any
) -> None:
    station = schedule_rows.station("st-excluded", simulated=False)
    chain = capability(rollback, station)
    held = [reception(schedule_rows, station, number) for number in range(6)]
    blocked = reception(schedule_rows, station, 6, loss_db=12.0)
    diagnose_as(rollback, blocked, "obstruction")
    # A cause that is the chain's own stays in.
    diagnose_as(rollback, held[0], "undetermined")

    build(rollback, station_id=station)

    found = current(rollback, station, chain)
    assert found is not None
    assert found.receptions == 6


def test_too_little_history_writes_nothing(rollback: Any, schedule_rows: Any) -> None:
    station = schedule_rows.station("st-thin", simulated=True)
    capability(rollback, station)
    for number in range(4):
        reception(schedule_rows, station, number)

    report = build(rollback, station_id=station)

    assert report.written == 0
    assert len(report.too_little) == 1


def test_a_reception_two_chains_cover_belongs_to_neither(
    rollback: Any, schedule_rows: Any
) -> None:
    station = schedule_rows.station("st-two-chains", simulated=False)
    capability(rollback, station)
    capability(rollback, station, low=137_500_000)
    for number in range(6):
        reception(schedule_rows, station, number)

    report = build(rollback, station_id=station)

    assert (report.written, report.chains, report.ambiguous) == (0, 0, 6)


def test_an_open_warning_holds_the_chain_back_from_training(
    rollback: Any, schedule_rows: Any
) -> None:
    station = schedule_rows.station("st-held", simulated=False)
    chain = capability(rollback, station)
    for number in range(6):
        reception(schedule_rows, station, number)
    old = insert_baseline(
        rollback,
        NewBaseline(
            station_id=station,
            capability_id=chain,
            evidence="samples",
            bin_width_deg=10.0,
            bins=[
                {
                    "elevation_deg": 10.0,
                    "median_db": 9.0,
                    "p10_db": 8.0,
                    "receptions": 5,
                    "samples": 50,
                }
            ],
            receptions=5,
            trained_from=TODAY - timedelta(days=80),
            trained_until=TODAY - timedelta(days=60),
            method="baseline-1",
            config_sha256=baseline_sha256(CONFIG),
        ),
    )
    raise_warning(
        rollback,
        NewWarning(
            baseline_id=old,
            raised_at=NOW - timedelta(days=1),
            shortfall_db=5.0,
            affected_bins=[],
            evidence={},
            method="watch-1",
            config_sha256=bytes(32),
        ),
    )

    held = build(rollback, station_id=station)
    rollback.execute(
        "update receive_chain_warnings set cleared_at = %s, cleared_shortfall_db = 0"
        " where baseline_id = %s",
        (NOW, old),
    )
    trained = build(rollback, station_id=station)

    assert (held.held, held.written) == (1, 0)
    assert trained.written == 1
    assert current(rollback, station, chain).id != old


def test_peaks_make_a_baseline_of_their_own(rollback: Any, schedule_rows: Any) -> None:
    station = schedule_rows.station("st-peaks", simulated=False)
    chain = capability(rollback, station)
    for number in range(5):
        reception(schedule_rows, station, number, samples=False)
    config = replace(CONFIG, baseline=BaselineConfig(min_bins=1))

    report = build(rollback, config, station_id=station)

    assert report.written == 1
    found = find_current_baseline(
        rollback,
        station_id=station,
        capability_id=chain,
        evidence="peak",
        method="baseline-1",
        config_sha256=baseline_sha256(config),
    )
    assert found is not None
    # Every pass peaks at 61.4°, heard at 11.5 dB: one bin.
    assert found.bins_json == [
        {
            "elevation_deg": 60.0,
            "median_db": 11.5,
            "p10_db": 11.5,
            "receptions": 5,
            "samples": 5,
        }
    ]
