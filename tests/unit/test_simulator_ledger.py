"""``meridian_sim.ledger`` — ground truth about a fault, written as it happened.

No marker: a ledger is a file, and what it has to get right — that it round-trips,
that a run dying part-way leaves it true, and that a reader refuses what it does
not understand — needs no platform.

Reference: docs/DECISIONS.md D-189.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from meridian_sim.ledger import (
    LEDGER_VERSION,
    FaultLedger,
    MalformedLedgerError,
    read_ledger,
)

AT = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


def ledger(tmp_path: Path) -> FaultLedger:
    """A ledger for one run, in a directory that does not exist yet."""
    return FaultLedger(tmp_path / "run" / "faults.jsonl", "run-1")


def test_a_window_round_trips(tmp_path: Path) -> None:
    """Opened and closed, with everything a verifier needs to find the station."""
    book = ledger(tmp_path)
    book.open(
        "clock_drift",
        "station:2",
        AT,
        tick=4,
        station_id="st_2",
        seed=77,
        detail={"drift_s_per_tick": 1.5},
    )
    book.close("clock_drift", "station:2", AT + timedelta(minutes=5), tick=14)

    (record,) = read_ledger(book.path)

    assert record.kind == "clock_drift"
    assert record.target == "station:2"
    assert record.station_id == "st_2"
    assert record.seed == 77
    assert (record.first_tick, record.last_tick) == (4, 14)
    assert record.opened_at == AT
    assert record.closed_at == AT + timedelta(minutes=5)
    assert record.detail == {"drift_s_per_tick": 1.5}


def test_a_fault_still_in_force_has_no_close(tmp_path: Path) -> None:
    """A revoked token, or a run that ended mid-fault."""
    book = ledger(tmp_path)
    book.open("token_revoked", "station:1", AT, tick=9)

    (record,) = read_ledger(book.path)

    assert record.closed_at is None
    assert record.last_tick is None


def test_what_a_fault_did_is_gathered_onto_its_window(tmp_path: Path) -> None:
    """Every assignment a declining station let go of, across the whole window."""
    book = ledger(tmp_path)
    book.open("declines", "station:3", AT, tick=1)
    book.act("declines", "station:3", AT, ("as_1", "as_2"), tick=1)
    book.act("declines", "station:3", AT, ("as_3",), tick=2)
    book.close("declines", "station:3", AT, tick=3)

    (record,) = read_ledger(book.path)

    assert record.assignment_ids == ("as_1", "as_2", "as_3")


def test_the_same_fault_can_open_again_after_it_closed(tmp_path: Path) -> None:
    """A recurring fault is many windows, in the order they opened."""
    book = ledger(tmp_path)
    for tick in (1, 10):
        book.open("slow_api", "station:1", AT, tick=tick)
        book.close("slow_api", "station:1", AT, tick=tick + 2)

    assert [one.first_tick for one in read_ledger(book.path)] == [1, 10]


def test_platform_faults_carry_no_station(tmp_path: Path) -> None:
    """Injected on the wall clock by the host tool, not by the supervisor."""
    book = ledger(tmp_path)
    book.open("platform_restart", "platform:api", AT)

    (record,) = read_ledger(book.path)

    assert (record.station_id, record.seed, record.first_tick) == (None, None, None)


def test_every_line_carries_the_format_version(tmp_path: Path) -> None:
    """So a reader meeting a line it does not know refuses it."""
    book = ledger(tmp_path)
    book.open("partition", "station:1", AT, detail={"members": [1, 4]})

    (line,) = book.path.read_text("utf-8").splitlines()

    assert json.loads(line)["ledger"] == LEDGER_VERSION


def test_an_instant_without_a_zone_is_refused(tmp_path: Path) -> None:
    """A ledger is set against the platform's UTC clock; a naive time cannot be."""
    with pytest.raises(ValueError, match="timezone"):
        ledger(tmp_path).open("slow_api", "station:1", datetime(2026, 9, 29))  # noqa: DTZ001


@pytest.mark.parametrize(
    "line",
    [
        "not json",
        json.dumps({"ledger": 99, "event": "open"}),
        json.dumps({"ledger": LEDGER_VERSION, "event": "reopen"}),
        json.dumps(
            {
                "ledger": LEDGER_VERSION,
                "event": "close",
                "run_id": "run-1",
                "kind": "slow_api",
                "target": "station:1",
                "at": "2026-09-29T12:00:00Z",
            }
        ),
        json.dumps(
            {
                "ledger": LEDGER_VERSION,
                "event": "open",
                "run_id": "run-1",
                "kind": "slow_api",
                "target": "station:1",
                "at": "2026-09-29T12:00:00Z",
                "tick": "four",
            }
        ),
    ],
    ids=["not-json", "other-version", "unknown-event", "close-unopened", "bad-tick"],
)
def test_a_line_the_reader_does_not_understand_is_refused(
    tmp_path: Path, line: str
) -> None:
    """Ground truth read in part would grade the platform against part of it."""
    path = tmp_path / "faults.jsonl"
    path.write_text(line + "\n", "utf-8")

    with pytest.raises(MalformedLedgerError, match="line 1"):
        read_ledger(path)


def test_a_window_opened_twice_is_refused(tmp_path: Path) -> None:
    """Two opens with no close between them is a ledger that lost a line."""
    book = ledger(tmp_path)
    book.open("slow_api", "station:1", AT)
    book.open("slow_api", "station:1", AT)

    with pytest.raises(MalformedLedgerError, match="line 2"):
        read_ledger(book.path)
