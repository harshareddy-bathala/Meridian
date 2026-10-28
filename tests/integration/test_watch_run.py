"""The watch end to end over the store: a step loss warns, one bad pass does not.

Marked ``integration`` by the directory hook. The orbit service is a stand-in
whose every pass climbs to 58° and back, and each reception's SNR is a quarter
of its elevation less a chosen loss. A chain is trained on a clean stretch
first, then judged as the jobs service judges it:
- a step loss raises one warning on its second reception, at the run's instant;
- one bad pass raises nothing;
- a recovered chain has its warning cleared, once;
- a run with nothing new writes nothing.

Reference: docs/DECISIONS.md D-292, D-293, D-294.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from meridian.orbit.types import LookAngle  # noqa: E402
from meridian.reliability.config import ReliabilityConfig  # noqa: E402
from meridian.reliability.watch_run import watch_chains  # noqa: E402
from meridian.store.receive_chain_warnings import find_warnings  # noqa: E402

pytestmark = pytest.mark.integration

CLEAN_FROM = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
TRAINED_AT = datetime(2026, 10, 15, 6, 0, tzinfo=UTC)
LATER = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)
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
                start + timedelta(seconds=k * step_s),
                90.0,
                elevation(k, count),
                900.0,
                0.0,
            )
            for k in range(count)
        ]


def elevation(k: int, count: int) -> float:
    return PEAK_DEG * math.sin(math.pi * k / max(count - 1, 1))


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def chain(rollback: Any, schedule_rows: Any) -> Any:
    """A simulated station with one VHF chain, heard cleanly six times."""
    station = schedule_rows.station("st-watch", simulated=True)
    rollback.execute(
        "insert into station_capabilities (station_id, band, freq_min_hz,"
        " freq_max_hz, modes, polarisation, min_elevation_deg)"
        " values (%s, 'vhf', 137000000, 138000000, '{lrpt}', 'rhcp', 5)",
        (station,),
    )
    element_set = schedule_rows.satellite()
    made = Chain(schedule_rows, station, element_set)
    for number in range(6):
        made.heard(CLEAN_FROM + timedelta(hours=8 * number))
    return made


class Chain:
    """Receptions of one station's chain, each at a chosen loss."""

    def __init__(self, rows: Any, station: str, element_set: int) -> None:
        self.rows, self.station, self.element_set = rows, station, element_set
        self.count = 0

    def heard(self, aos: datetime, loss_db: float = 0.0) -> str:
        self.count += 1
        pass_id = self.rows.pass_(
            self.station, aos, element_set_id=self.element_set, minutes=10
        )
        assignment = self.rows.assignment(
            f"{self.station}-{self.count}", pass_id, state="reported"
        )
        self.rows.observation(assignment)
        samples = int(WINDOW.total_seconds() // STEP_S) + 1
        series = [
            {
                "t": (aos + timedelta(seconds=k * STEP_S)).isoformat(),
                "snr_db": round(elevation(k, samples + 1) / 4 - loss_db, 2),
            }
            for k in range(samples)
        ]
        self.rows.conn.execute(
            "update observations set snr_samples = %s where assignment_id = %s",
            (json.dumps(series), assignment),
        )
        return assignment


def watch(conn: Any, now: datetime) -> Any:
    return watch_chains(conn, Overhead(), config=CONFIG, now=now)  # type: ignore[arg-type]


def test_a_step_loss_raises_one_warning_on_its_second_reception(
    rollback: Any, chain: Chain
) -> None:
    trained = watch(rollback, TRAINED_AT)
    chain.heard(LATER, loss_db=6.0)
    after_one = watch(rollback, LATER + timedelta(hours=1))
    chain.heard(LATER + timedelta(hours=3), loss_db=6.0)
    raised_at = LATER + timedelta(hours=4)
    after_two = watch(rollback, raised_at)

    assert trained.baselines.written == 1
    assert (trained.judged, trained.raised) == (1, ())
    assert after_one.raised == ()
    assert len(after_two.raised) == 1
    (warning,) = find_warnings(rollback, chain.station)
    assert warning.raised_at == raised_at
    assert warning.cleared_at is None
    assert warning.simulated is True
    assert 5.5 <= warning.shortfall_db <= 6.5
    assert len(warning.affected_bins_json) >= 2
    assert (
        warning.evidence_json["through"]
        == (LATER + timedelta(hours=3, minutes=10)).isoformat()
    )


def test_one_bad_pass_raises_nothing(rollback: Any, chain: Chain) -> None:
    watch(rollback, TRAINED_AT)
    chain.heard(LATER, loss_db=12.0)
    chain.heard(LATER + timedelta(hours=3))

    report = watch(rollback, LATER + timedelta(hours=4))

    assert report.raised == ()
    assert find_warnings(rollback, chain.station) == []


def test_a_recovered_chain_is_cleared_once(rollback: Any, chain: Chain) -> None:
    watch(rollback, TRAINED_AT)
    for hours in (0, 3):
        chain.heard(LATER + timedelta(hours=hours), loss_db=6.0)
    watch(rollback, LATER + timedelta(hours=4))
    for hours in (6, 9, 12):
        chain.heard(LATER + timedelta(hours=hours))
    cleared_at = LATER + timedelta(hours=13)

    cleared = watch(rollback, cleared_at)
    again = watch(rollback, cleared_at + timedelta(hours=1))

    assert len(cleared.cleared) == 1
    assert (again.raised, again.cleared, again.open) == ((), (), 0)
    (warning,) = find_warnings(rollback, chain.station)
    assert warning.cleared_at == cleared_at
    assert warning.cleared_shortfall_db is not None
    assert warning.cleared_shortfall_db <= CONFIG.watch.clear_db


def test_a_run_with_nothing_new_writes_nothing(rollback: Any, chain: Chain) -> None:
    watch(rollback, TRAINED_AT)
    for hours in (0, 3):
        chain.heard(LATER + timedelta(hours=hours), loss_db=6.0)
    watch(rollback, LATER + timedelta(hours=4))

    again = watch(rollback, LATER + timedelta(hours=5))

    assert (again.raised, again.cleared, again.open) == ((), (), 1)
    assert len(find_warnings(rollback, chain.station)) == 1
