"""``meridian jobs run`` refuses a configuration that would schedule wrongly.

Every refusal here happens before a database connection is attempted, which is
what keeps these unit tests: the process must say what is wrong and exit, not
start, connect and then fail in a way that reads as a database outage.

Marked as a unit test by living in ``tests/unit``: no network, no database.

Reference: docs/DECISIONS.md D-109, D-110.
"""

from __future__ import annotations

import argparse
import subprocess
import sys

import pytest

from meridian import cli_jobs
from meridian.cli_jobs import run_jobs


@pytest.fixture(autouse=True)
def _no_real_work(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the test if a refusal case ever gets as far as building the tasks."""
    for variable in (
        "SCHEDULE_INTERVAL_S",
        "SCHEDULE_HORIZON_S",
        "PROMETHEUS_MULTIPROC_DIR",
        "API_LOG_LEVEL",
    ):
        monkeypatch.delenv(variable, raising=False)

    def refuse(_settings: object) -> None:
        raise AssertionError("a refused configuration must not build its tasks")

    monkeypatch.setattr(cli_jobs, "_rounds", refuse)


def test_the_command_tree_does_not_load_the_jobs_metrics() -> None:
    """API workers must never register the jobs process's series.

    ``meridian serve`` spawns each worker by re-importing the command tree. When
    ``cli_jobs`` imported ``meridian.jobs`` at the top, every worker registered
    ``meridian_passes_computed`` and the API published it as 0 once per worker —
    a zero from a process that never computes passes (D-086). A fresh
    interpreter, because this test process may have imported them already.
    """
    probe = (
        "import sys, meridian.cli; "
        "print(sorted(m for m in sys.modules if m.startswith('meridian.jobs')))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == "[]"


@pytest.mark.parametrize(
    ("environment", "reason"),
    [
        ({"SCHEDULE_INTERVAL_S": "0"}, "SCHEDULE_INTERVAL_S"),
        (
            {"SCHEDULE_INTERVAL_S": "600", "SCHEDULE_HORIZON_S": "600"},
            "SCHEDULE_HORIZON_S must exceed",
        ),
        ({"PROMETHEUS_MULTIPROC_DIR": "/run/metrics"}, "PROMETHEUS_MULTIPROC_DIR"),
        ({"API_LOG_LEVEL": "loud"}, "API_LOG_LEVEL"),
    ],
)
def test_a_configuration_that_would_schedule_wrongly_is_refused(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    environment: dict[str, str],
    reason: str,
) -> None:
    """Exit 1 with the reason; nothing is built and nothing connects."""
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    result = run_jobs(argparse.Namespace(once=True, metrics_host="127.0.0.1"))

    assert result == 1
    assert reason in capsys.readouterr().err
