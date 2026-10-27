"""``meridian watch`` — what a baseline run prints.

Reference: docs/DECISIONS.md D-290, D-293.
"""

from __future__ import annotations

from datetime import UTC, datetime

from meridian.cli import _build_parser
from meridian.cli_watch import baseline_lines
from meridian.reliability.watch_build import BaselineRunReport

REPORT = BaselineRunReport(
    method="baseline-1",
    config_sha256=bytes.fromhex("ab" * 32),
    trained_from=datetime(2026, 10, 6, tzinfo=UTC),
    trained_until=datetime(2026, 10, 20, tzinfo=UTC),
    chains=3,
    written=1,
    kept=1,
    held=0,
    too_little=("st-b capability 4 (samples)",),
    ambiguous=2,
    simulated=1,
)


def test_a_run_says_what_it_trained_and_what_it_could_not() -> None:
    lines = baseline_lines(REPORT)

    assert lines[0] == "baselines by baseline-1, settings abababababab"
    assert "  interval           2026-10-06 to 2026-10-20" in lines
    assert "  written            1 (1 simulated)" in lines
    assert any("2 receptions no single capability covers" in one for one in lines)
    assert "  too little history st-b capability 4 (samples)" in lines


def test_the_command_takes_one_station() -> None:
    args = _build_parser().parse_args(["watch", "baseline", "--station", "st-a"])

    assert (args.command, args.action, args.station) == ("watch", "baseline", "st-a")
