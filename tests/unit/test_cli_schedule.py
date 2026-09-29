"""``meridian schedule`` — what it says when a run stops before writing.

A run that fails midway rolls back, and the command says so and exits 1,
rather than handing the operator a traceback: the model could not score a
pass, a pass names an element set that is gone, or the schedule broke its own
constraints (D-166, D-169).

Reference: docs/DECISIONS.md D-166, D-169.
"""

from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any

import pytest

from meridian import cli_schedule
from meridian.cli import main
from meridian.prediction.live import LiveScoringError
from meridian.scheduler.run import ScheduleInvalidError

HORIZON = ["--from", "2026-09-29T00:00:00Z", "--to", "2026-09-29T06:00:00Z"]


@pytest.mark.parametrize(
    "failure",
    [
        LiveScoringError("pass 7's rise has no geometry for [8]"),
        LookupError("element set 12 is gone"),
        ValueError("the model gave pass 7 no yield"),
        ScheduleInvalidError([]),
    ],
)
def test_a_run_that_stops_says_why_and_exits_1(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: Exception,
) -> None:
    def stops(*_: Any) -> None:
        raise failure

    monkeypatch.setattr(cli_schedule, "load_settings", SimpleNamespace)
    monkeypatch.setattr(cli_schedule, "connect_once", lambda _s: nullcontext())
    monkeypatch.setattr(cli_schedule, "run_schedule", stops)

    code = main(["schedule", *HORIZON])

    err = capsys.readouterr().err
    assert code == 1
    assert err.startswith("meridian schedule: the run stopped before writing")
    assert str(failure) in err


def test_a_defect_that_is_not_the_run_s_to_explain_is_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Positive control: only the named failures become a refusal."""

    def breaks(*_: Any) -> None:
        raise ZeroDivisionError

    monkeypatch.setattr(cli_schedule, "load_settings", SimpleNamespace)
    monkeypatch.setattr(cli_schedule, "connect_once", lambda _s: nullcontext())
    monkeypatch.setattr(cli_schedule, "run_schedule", breaks)

    with pytest.raises(ZeroDivisionError):
        main(["schedule", *HORIZON])
