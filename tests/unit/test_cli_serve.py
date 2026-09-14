"""``meridian serve`` hands uvicorn what the deployment configured, or refuses.

No server is started: ``uvicorn.run`` is replaced with a recorder, so what is
asserted is the call the command would make — the worker count, the log level
and the shutdown timeout — and the conditions under which it makes none.

Marked as a unit test by living in ``tests/unit``: no network, no database.

Reference: docs/DECISIONS.md D-109, D-114.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from meridian import cli_serve
from meridian.cli_serve import (
    APPLICATION,
    GRACEFUL_SHUTDOWN_TIMEOUT_S,
    logging_configuration,
    prepare_multiprocess_directory,
    run_serve,
)


@pytest.fixture
def uvicorn_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Record every ``uvicorn.run`` call instead of serving."""
    calls: list[dict[str, object]] = []

    def record(application: str, **options: object) -> None:
        calls.append({"application": application, **options})

    monkeypatch.setattr(cli_serve.uvicorn, "run", record)
    # dictConfig replaces the handlers pytest's log capture installed, and would
    # outlive this test; the configuration it is given is asserted directly.
    monkeypatch.setattr(cli_serve.logging.config, "dictConfig", lambda _: None)
    monkeypatch.delenv("PROMETHEUS_MULTIPROC_DIR", raising=False)
    monkeypatch.delenv("API_WORKERS", raising=False)
    monkeypatch.delenv("API_LOG_LEVEL", raising=False)
    return calls


def arguments(**overrides: object) -> argparse.Namespace:
    """What argparse produces for ``meridian serve`` with nothing given."""
    return argparse.Namespace(
        **({"host": "127.0.0.1", "port": None, "workers": None} | overrides)
    )


def test_defaults_come_from_the_settings(
    uvicorn_calls: list[dict[str, object]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``API_LOG_LEVEL`` reaches uvicorn at last, with the shutdown timeout."""
    monkeypatch.setenv("API_LOG_LEVEL", "WARNING")

    assert run_serve(arguments()) == 0

    (call,) = uvicorn_calls
    assert call["application"] == APPLICATION
    assert call["workers"] == 1
    assert call["port"] == 8000
    assert call["log_level"] == "warning"
    assert call["timeout_graceful_shutdown"] == GRACEFUL_SHUTDOWN_TIMEOUT_S


def test_several_workers_start_when_the_metrics_directory_is_set(
    uvicorn_calls: list[dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The directory is created, and the worker count passes through."""
    directory = tmp_path / "metrics"
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(directory))
    monkeypatch.setenv("API_WORKERS", "3")

    assert run_serve(arguments()) == 0

    assert uvicorn_calls[0]["workers"] == 3
    assert directory.is_dir()


@pytest.mark.parametrize(
    "case",
    [
        ("API_WORKERS", "2", "PROMETHEUS_MULTIPROC_DIR"),
        ("API_WORKERS", "0", "at least 1"),
        ("API_LOG_LEVEL", "loud", "API_LOG_LEVEL"),
    ],
)
def test_a_configuration_that_would_mislead_is_refused(
    uvicorn_calls: list[dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    case: tuple[str, str, str],
) -> None:
    """Refused before uvicorn is called, with the reason on standard error.

    Each case is a variable, the value that is refused, and a phrase the reason
    must contain.
    """
    variable, value, reason = case
    monkeypatch.setenv(variable, value)

    assert run_serve(arguments()) == 1

    assert uvicorn_calls == []
    assert reason in capsys.readouterr().err


def test_a_previous_runs_metric_files_are_removed_and_nothing_else(
    tmp_path: Path,
) -> None:
    """Counts from a finished run would otherwise be summed into this one."""
    (tmp_path / "counter_12.db").write_bytes(b"old")
    (tmp_path / "keep.txt").write_text("not ours")

    prepare_multiprocess_directory(tmp_path)

    assert sorted(path.name for path in tmp_path.iterdir()) == ["keep.txt"]


@pytest.mark.parametrize(
    ("level", "python_level"), [("debug", "DEBUG"), ("trace", "DEBUG")]
)
def test_uvicorn_and_the_platform_log_through_one_handler(
    level: str, python_level: str
) -> None:
    """One root handler; uvicorn's loggers add none and propagate to it.

    Asserted on the document rather than by applying it, because applying it
    would replace the handlers pytest's own log capture relies on. ``trace`` is
    uvicorn's level below debug, which Python's logging does not have.
    """
    configuration = logging_configuration(level)

    root = configuration["root"]
    assert root == {"handlers": ["stderr"], "level": python_level}
    loggers = configuration["loggers"]
    assert isinstance(loggers, dict)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        assert loggers[name] == {
            "handlers": [],
            "level": python_level,
            "propagate": True,
        }
    assert loggers["alembic"]["level"] == "WARNING"
