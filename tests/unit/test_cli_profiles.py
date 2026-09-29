"""``meridian profiles build`` — its wiring, and what it says about each outcome.

The build itself is tested against a database in
``tests/integration/test_profile_build.py``. This pins what an operator reads.

Marked as a unit test by living in ``tests/unit``.

Reference: docs/DECISIONS.md D-174.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from meridian.cli import IMPLEMENTED, NEEDS_ACTION, _build_parser
from meridian.cli_profiles import print_profile_report, run_profiles
from meridian.profile_build import ProfileBuildReport

AS_OF = datetime(2026, 9, 23, 6, 0, tzinfo=UTC)


def report(**changes: object) -> ProfileBuildReport:
    base = {
        "declared_capabilities": 2,
        "declared_written": 1,
        "dataset": Path("/datasets/evaluation/20260923T060000Z-abc"),
        "dataset_as_of": AS_OF,
        "already_built": False,
        "stations_built": 3,
        "horizon_rows": 108,
        "interference_rows": 144,
    }
    base.update(changes)
    return ProfileBuildReport(**base)  # type: ignore[arg-type]


def test_the_command_is_wired_and_needs_its_verb() -> None:
    args = _build_parser().parse_args(["profiles", "--root", "/d", "build"])

    assert (args.command, args.action, args.root) == ("profiles", "build", Path("/d"))
    assert IMPLEMENTED["profiles"] is run_profiles
    assert "profiles" in NEEDS_ACTION


def test_a_build_says_what_it_wrote(capsys: pytest.CaptureFixture[str]) -> None:
    print_profile_report(report())

    out = capsys.readouterr().out
    assert "declared masks:      2 (1 changed and written)" in out
    assert "stations built:      3" in out
    assert "interference rows:   144" in out


def test_a_second_build_says_already_held(capsys: pytest.CaptureFixture[str]) -> None:
    print_profile_report(report(already_built=True, declared_written=0))

    out = capsys.readouterr().out
    assert "already held, identically" in out
    assert "stations built" not in out


def test_with_no_dataset_it_says_so(capsys: pytest.CaptureFixture[str]) -> None:
    print_profile_report(report(dataset=None, dataset_as_of=None))

    assert "no labelled dataset yet" in capsys.readouterr().out
