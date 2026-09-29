"""The nightly backup's retention policy and names (D-209), without Docker.

``kept`` decides which dumps survive, and ``prune`` deletes the rest. A mistake
in either deletes a backup that was needed, so the policy is pinned date by
date, and ``prune`` is shown to leave alone anything it did not name itself.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "deploy/tools"
NOW = datetime(2026, 9, 28, 2, 47, tzinfo=UTC)
POLICY = {"daily": 7, "weekly": 4, "monthly": 6}


@pytest.fixture(scope="module")
def schedule() -> Iterator[ModuleType]:
    sys.path.insert(0, str(TOOLS))
    try:
        spec = importlib.util.spec_from_file_location(
            "scheduled_backup", TOOLS / "scheduled_backup.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.path.remove(str(TOOLS))
        for name in ("compose_db", "backup"):
            sys.modules.pop(name, None)


def _nightly(days: int) -> list[datetime]:
    return [NOW - timedelta(days=n) for n in range(days)]


def test_a_name_is_its_time_and_reads_back(schedule: ModuleType) -> None:
    name = schedule.dump_name(NOW)

    assert name == "meridian-2026-09-28T0247Z.dump"
    assert schedule.taken_at(name) == NOW
    assert schedule.taken_at("meridian-before-the-upgrade.dump") is None


def test_a_year_of_nightly_dumps_keeps_seventeen_at_most(schedule: ModuleType) -> None:
    kept = schedule.kept(_nightly(365), NOW, **POLICY)

    assert len(kept) <= 7 + 4 + 6
    assert set(_nightly(7)) <= kept


def test_the_newest_of_each_recent_week_and_month_is_kept(
    schedule: ModuleType,
) -> None:
    stamps = _nightly(200)
    kept = schedule.kept(stamps, NOW, **POLICY)

    for weeks_ago in range(4):
        week = (NOW - timedelta(weeks=weeks_ago)).isocalendar()[:2]
        in_week = [s for s in stamps if s.isocalendar()[:2] == week]
        assert max(in_week) in kept
    for months_ago in range(6):
        month = (NOW.year * 12 + NOW.month - 1 - months_ago) % 12 + 1
        in_month = [s for s in stamps if s.month == month]
        assert max(in_month) in kept


def test_the_newest_dump_is_kept_however_old(schedule: ModuleType) -> None:
    """A host that was off for a year still has its last backup."""
    ancient = [NOW - timedelta(days=400), NOW - timedelta(days=500)]

    assert NOW - timedelta(days=400) in schedule.kept(ancient, NOW, **POLICY)


def test_prune_deletes_only_what_it_named_and_the_policy_drops(
    schedule: ModuleType, tmp_path: Path
) -> None:
    for stamp in _nightly(60):
        dump = tmp_path / schedule.dump_name(stamp)
        dump.write_bytes(b"dump")
        (tmp_path / (dump.name + ".manifest.json")).write_text("{}")
    by_hand = tmp_path / "meridian-before-the-upgrade.dump"
    by_hand.write_bytes(b"kept")

    removed = schedule.prune(tmp_path, NOW, **POLICY)

    remaining = sorted(p.name for p in tmp_path.glob("*.dump"))
    assert removed
    assert by_hand.exists()
    assert all(not p.exists() for p in removed)
    assert all(not (p.parent / (p.name + ".manifest.json")).exists() for p in removed)
    assert len(remaining) == len(schedule.kept(_nightly(60), NOW, **POLICY)) + 1
