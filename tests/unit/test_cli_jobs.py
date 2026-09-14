"""``meridian jobs run`` refuses a configuration that would schedule wrongly.

Every refusal here happens before a database connection is attempted, which is
what keeps these unit tests: the process must say what is wrong and exit, not
start, connect and then fail in a way that reads as a database outage.

Marked as a unit test by living in ``tests/unit``: no network, no database.

Reference: docs/DECISIONS.md D-109, D-110.
"""

from __future__ import annotations

import argparse

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
