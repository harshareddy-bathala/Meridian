"""``meridian watch`` — what a run prints, and how a warning reads.

Reference: docs/DECISIONS.md D-290, D-293, D-294.
"""

from __future__ import annotations

from datetime import UTC, datetime

from meridian.cli import _build_parser
from meridian.cli_watch import baseline_lines, run_lines, warning_lines
from meridian.reliability.watch_build import BaselineRunReport
from meridian.reliability.watch_run import WatchRunReport
from meridian.store.receive_chain_warnings import StoredWarning

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


def test_a_watch_run_says_what_it_raised() -> None:
    report = WatchRunReport(
        method="watch-1",
        config_sha256=bytes.fromhex("cd" * 32),
        baselines=REPORT,
        judged=2,
        no_baseline=1,
        too_few=0,
        raised=("st-a capability 1: 4.2 dB short",),
        open=1,
    )

    lines = run_lines(report)

    assert lines[0].startswith("baselines by baseline-1")
    assert "watch by watch-1, settings cdcdcdcdcdcd" in lines
    assert "  raised             st-a capability 1: 4.2 dB short" in lines
    assert "  open after the run 1" in lines


def test_a_station_with_no_warning_says_so() -> None:
    assert warning_lines("st-a", []) == ["st-a: no receive-chain warning"]


def test_a_warning_is_printed_with_its_bins() -> None:
    raised = datetime(2026, 10, 15, 13, tzinfo=UTC)
    row = StoredWarning(
        id=1,
        station_id="st-a",
        capability_id=4,
        baseline_id=9,
        raised_at=raised,
        shortfall_db=4.2,
        affected_bins_json=[
            {"elevation_deg": 20.0, "shortfall_db": 4.4, "receptions": 3}
        ],
        evidence_json={},
        cleared_at=None,
        cleared_shortfall_db=None,
        method="watch-1",
        config_sha256=bytes(32),
        simulated=True,
    )

    assert warning_lines("st-a", [row]) == [
        "capability 4 (simulated): raised 2026-10-15 13:00 UTC, 4.2 dB short,"
        " open  [watch-1, baseline 9]",
        "     20.0°  4.4 dB short over 3 receptions",
    ]
