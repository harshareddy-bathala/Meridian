"""The receive-chain watch's two tables — what the store and schema allow.

Marked ``integration`` by the directory hook. What only a database shows:
- a baseline is written once per chain, evidence, method, configuration and
  interval, and takes ``simulated`` from its station;
- the latest baseline is the one whose interval ends last;
- one warning is open per chain at a time;
- a warning is cleared once, with the shortfall it recovered to;
- a warning can only name its own station's baseline, with the same
  ``simulated`` (D-291);
- the table refuses half a clearing.

Reference: docs/DECISIONS.md D-290, D-291, D-292.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from meridian.store.receive_chain_warnings import (  # noqa: E402
    NewWarning,
    clear_warning,
    find_open_warnings,
    find_warnings,
    raise_warning,
)
from meridian.store.signal_baselines import (  # noqa: E402
    NewBaseline,
    find_current_baseline,
    insert_baseline,
)

pytestmark = pytest.mark.integration

SINCE = datetime(2026, 10, 1, tzinfo=UTC)
UNTIL = datetime(2026, 10, 8, tzinfo=UTC)
RAISED = datetime(2026, 10, 12, 6, 0, tzinfo=UTC)
CONFIG = bytes([3]) * 32
BINS: list[dict[str, object]] = [
    {
        "elevation_deg": 10.0,
        "median_db": 6.5,
        "p10_db": 4.0,
        "receptions": 9,
        "samples": 140,
    },
    {
        "elevation_deg": 20.0,
        "median_db": 9.0,
        "p10_db": 7.1,
        "receptions": 8,
        "samples": 96,
    },
]


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    with conn.transaction(force_rollback=True):
        yield conn


def capability(conn: Any, station_id: str) -> int:
    """One VHF receive chain on ``station_id``."""
    with conn.cursor() as cur:
        cur.execute(
            "insert into station_capabilities (station_id, band, freq_min_hz,"
            " freq_max_hz, modes, polarisation, min_elevation_deg)"
            " values (%s, 'vhf', 137000000, 138000000, '{lrpt}', 'rhcp', 5)"
            " returning id",
            (station_id,),
        )
        return int(cur.fetchone()[0])


def baseline(station_id: str, capability_id: int, **changes: Any) -> NewBaseline:
    return replace(
        NewBaseline(
            station_id=station_id,
            capability_id=capability_id,
            evidence="samples",
            bin_width_deg=10.0,
            bins=BINS,
            receptions=17,
            trained_from=SINCE,
            trained_until=UNTIL,
            method="baseline-1",
            config_sha256=CONFIG,
        ),
        **changes,
    )


def warning(baseline_id: int, **changes: Any) -> NewWarning:
    return replace(
        NewWarning(
            baseline_id=baseline_id,
            raised_at=RAISED,
            shortfall_db=4.2,
            affected_bins=[
                {"elevation_deg": 10.0, "shortfall_db": 4.2, "receptions": 3}
            ],
            evidence={"receptions": ["a-1", "a-2", "a-3"]},
            method="watch-1",
            config_sha256=CONFIG,
        ),
        **changes,
    )


def test_a_baseline_is_written_once_and_found_again(
    rollback: Any, schedule_rows: Any
) -> None:
    station = schedule_rows.station("st-base-once", simulated=False)
    chain = capability(rollback, station)

    first = insert_baseline(rollback, baseline(station, chain))
    again = insert_baseline(rollback, baseline(station, chain))

    assert again == first
    count = rollback.execute(
        "select count(*) from signal_baselines where station_id = %s", (station,)
    ).fetchone()[0]
    assert count == 1


def test_a_baseline_takes_simulated_from_its_station(
    rollback: Any, schedule_rows: Any
) -> None:
    measured = schedule_rows.station("st-base-measured", simulated=False)
    simulated = schedule_rows.station("st-base-simulated", simulated=True)

    for station in (measured, simulated):
        insert_baseline(rollback, baseline(station, capability(rollback, station)))

    rows = dict(
        rollback.execute(
            "select station_id, simulated from signal_baselines"
            " where station_id = any(%s)",
            ([measured, simulated],),
        ).fetchall()
    )
    assert rows == {measured: False, simulated: True}


def test_a_baseline_for_no_station_is_refused(
    rollback: Any, schedule_rows: Any
) -> None:
    station = schedule_rows.station("st-base-real", simulated=False)
    chain = capability(rollback, station)

    with pytest.raises(LookupError, match="no station"):
        insert_baseline(rollback, baseline("st-nobody", chain))


def test_the_current_baseline_is_the_one_that_ends_last(
    rollback: Any, schedule_rows: Any
) -> None:
    station = schedule_rows.station("st-base-latest", simulated=False)
    chain = capability(rollback, station)
    later = insert_baseline(
        rollback,
        baseline(
            station, chain, trained_from=UNTIL, trained_until=UNTIL + timedelta(7)
        ),
    )
    # Built afterwards, over an earlier interval: it must not displace the later.
    insert_baseline(
        rollback,
        baseline(
            station, chain, trained_from=SINCE - timedelta(7), trained_until=SINCE
        ),
    )

    found = find_current_baseline(
        rollback,
        station_id=station,
        capability_id=chain,
        evidence="samples",
        method="baseline-1",
        config_sha256=CONFIG,
    )

    assert found is not None
    assert found.id == later
    assert found.bins_json == BINS
    assert found.simulated is False


def test_the_two_kinds_of_evidence_are_separate_baselines(
    rollback: Any, schedule_rows: Any
) -> None:
    station = schedule_rows.station("st-base-kinds", simulated=False)
    chain = capability(rollback, station)

    samples = insert_baseline(rollback, baseline(station, chain))
    peak = insert_baseline(rollback, baseline(station, chain, evidence="peak"))

    assert samples != peak


def test_one_warning_is_open_per_chain(rollback: Any, schedule_rows: Any) -> None:
    station = schedule_rows.station("st-warn-open", simulated=True)
    chain = capability(rollback, station)
    base = insert_baseline(rollback, baseline(station, chain))

    first = raise_warning(rollback, warning(base))
    second = raise_warning(rollback, warning(base, raised_at=RAISED + timedelta(1)))

    assert first is not None
    assert second is None
    (open_,) = find_open_warnings(rollback, method="watch-1", config_sha256=CONFIG)
    assert (open_.id, open_.station_id, open_.capability_id) == (first, station, chain)
    assert open_.simulated is True


def test_a_warning_is_cleared_once_and_another_may_follow(
    rollback: Any, schedule_rows: Any
) -> None:
    station = schedule_rows.station("st-warn-clear", simulated=False)
    chain = capability(rollback, station)
    base = insert_baseline(rollback, baseline(station, chain))
    first = raise_warning(rollback, warning(base))
    assert first is not None

    cleared = clear_warning(rollback, first, at=RAISED + timedelta(1), shortfall_db=0.4)
    again = clear_warning(rollback, first, at=RAISED + timedelta(2), shortfall_db=0.1)
    second = raise_warning(rollback, warning(base, raised_at=RAISED + timedelta(3)))

    assert (cleared, again) == (True, False)
    assert second is not None
    history = find_warnings(rollback, station)
    assert [one.id for one in history] == [first, second]
    assert history[0].cleared_at == RAISED + timedelta(1)
    assert history[0].cleared_shortfall_db == 0.4
    assert history[1].cleared_at is None


def test_a_warning_for_no_baseline_is_refused(rollback: Any) -> None:
    with pytest.raises(LookupError, match="no baseline"):
        raise_warning(rollback, warning(-1))


def test_a_warning_cannot_name_another_stations_baseline(
    rollback: Any, schedule_rows: Any
) -> None:
    """The key, not the store, refuses a measured warning on a simulated baseline."""
    simulated = schedule_rows.station("st-key-simulated", simulated=True)
    measured = schedule_rows.station("st-key-measured", simulated=False)
    base = insert_baseline(
        rollback, baseline(simulated, capability(rollback, simulated))
    )
    chain = capability(rollback, measured)

    with pytest.raises(psycopg.errors.ForeignKeyViolation), rollback.transaction():
        rollback.execute(
            "insert into receive_chain_warnings (station_id, capability_id,"
            " baseline_id, raised_at, shortfall_db, affected_bins_json,"
            " evidence_json, method, config_sha256, simulated)"
            " values (%s, %s, %s, %s, 3, '[]', '{}', 'watch-1', %s, false)",
            (measured, chain, base, RAISED, CONFIG),
        )


def test_half_a_clearing_is_refused(rollback: Any, schedule_rows: Any) -> None:
    station = schedule_rows.station("st-warn-half", simulated=False)
    base = insert_baseline(rollback, baseline(station, capability(rollback, station)))
    raised = raise_warning(rollback, warning(base))

    with pytest.raises(psycopg.errors.CheckViolation), rollback.transaction():
        rollback.execute(
            "update receive_chain_warnings set cleared_at = %s where id = %s",
            (RAISED + timedelta(1), raised),
        )
