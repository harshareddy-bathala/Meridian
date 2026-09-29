"""``meridian schedule runs`` and ``meridian passes timing`` — wiring and output.

The views are tested with rows in ``tests/integration``. This pins what an
operator reads, and that each verb reaches its handler.

Marked as a unit test by living in ``tests/unit``.

Reference: docs/DECISIONS.md D-177.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from meridian import cli_passes, cli_schedule
from meridian.cli import _build_parser
from meridian.cli_views import print_runs, print_timing
from meridian.store.operator_views import RunPerformance, TimingError

AT = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)


def run(**changes: object) -> RunPerformance:
    base: dict[str, object] = {
        "run_id": "sr_000000000001",
        "decided_at": AT,
        "model_config": "A",
        "yield_source": "elevation_proxy",
        "solver_status": "optimal",
        "fell_back": False,
        "runtime_s": 0.04,
        "candidates": 7,
        "scheduled": 5,
        "skipped": 2,
        "revoked": 1,
        "expired": 0,
        "decoded": 3,
        "signal_no_decode": 0,
        "no_signal": 1,
        "aborted": 0,
        "not_attempted": 0,
        "outstanding": 0,
        "frames_decoded": 412,
        "assignments": 5,
        "simulated": True,
    }
    base.update(changes)
    return RunPerformance(**base)  # type: ignore[arg-type]


def timing(**changes: object) -> TimingError:
    base: dict[str, object] = {
        "assignment_id": "as_1",
        "station_id": "st_1",
        "satellite_id": "norad:57166",
        "aos": AT,
        "uncorrected_error_s": 40.0,
        "timing_error_s": 38.0,
        "clock_uncertainty_s": 0.5,
        "element_set_age_days": 1.25,
        "excluded": None,
        "simulated": False,
    }
    base.update(changes)
    return TimingError(**base)  # type: ignore[arg-type]


def test_the_two_verbs_parse() -> None:
    parser = _build_parser()

    runs = parser.parse_args(["schedule", "runs", "--limit", "5"])
    timed = parser.parse_args(["passes", "timing", "--station", "st_1"])

    assert (runs.action, runs.limit) == ("runs", 5)
    assert (timed.action, timed.station, timed.limit) == ("timing", "st_1", 20)


def test_each_verb_reaches_its_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []
    monkeypatch.setattr(
        cli_schedule, "run_schedule_runs", lambda _args: called.append("runs") or 0
    )
    monkeypatch.setattr(
        cli_passes, "run_pass_timing", lambda _args: called.append("timing") or 0
    )
    parser = _build_parser()

    cli_schedule.run_scheduler(parser.parse_args(["schedule", "runs"]))
    cli_passes.run_passes(parser.parse_args(["passes", "timing"]))

    assert called == ["runs", "timing"]


def test_a_run_says_what_came_of_it(capsys: pytest.CaptureFixture[str]) -> None:
    print_runs([run(), run(run_id="sr_000000000002", fell_back=True)])

    out = capsys.readouterr().out
    assert "sr_000000000001" in out
    assert "simulated" in out
    assert "fell back to greedy" in out


def test_no_run_yet_is_said_plainly(capsys: pytest.CaptureFixture[str]) -> None:
    print_runs([])

    assert "no schedule run is recorded yet" in capsys.readouterr().out


def test_timing_names_the_correction_and_every_exclusion(
    capsys: pytest.CaptureFixture[str],
) -> None:
    print_timing(
        [
            timing(),
            timing(
                assignment_id="as_2",
                timing_error_s=None,
                excluded="clock_offset_unknown",
            ),
        ]
    )

    out = capsys.readouterr().out
    assert "+38.0" in out
    assert "clock_offset_unknown" in out
    assert "not a reported figure" in out
