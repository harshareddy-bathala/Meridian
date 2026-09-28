"""``meridian reliability`` through ``meridian.cli.main``, against real TimescaleDB.

The command opens its own connection, which could not see rows written inside a
test's rolled-back transaction, so ``connect_once`` is handed that transaction's
connection instead. Everything else is the real command: the parser, the
configuration, the registry and the store.

What is pinned is Stage 20's gate at a prompt: ``classify`` stores what it
decided, ``report`` counts it, and ``explain`` traces one pass back to the
assignment, the report and the heartbeat evidence behind it.

Reference: docs/DECISIONS.md D-182, D-183, D-184.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

pytest.importorskip("psycopg")

from meridian import cli_reliability
from meridian.cli import main

pytestmark = pytest.mark.integration

AOS = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture(autouse=True)
def this_connection(rollback: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    @contextmanager
    def lend(_settings: object) -> Iterator[Any]:
        yield rollback

    monkeypatch.setattr(cli_reliability, "connect_once", lend)
    monkeypatch.delenv("MERIDIAN_RELIABILITY_CONFIG", raising=False)


@pytest.fixture
def world(schedule_rows: Any) -> None:
    """A measured station that decoded one pass and was absent for another."""
    rows = schedule_rows
    elements = rows.satellite("norad:99970")
    rows.station("st_a", simulated=False)
    rows.assignment(
        "as_heard",
        rows.pass_("st_a", AOS, element_set_id=elements),
        state="reported",
    )
    rows.observation("as_heard", outcome="decoded")
    rows.assignment(
        "as_absent",
        rows.pass_("st_a", AOS + timedelta(hours=2), element_set_id=elements),
    )


@pytest.mark.usefixtures("world")
def test_sweep_then_classify_then_report_then_explain(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["reliability", "sweep"]) == 0
    assert "expired 1 scheduled assignments" in capsys.readouterr().out

    assert main(["reliability", "classify"]) == 0
    classified = capsys.readouterr().out
    assert "classified 2 passes" in classified
    assert "station_unavailable" in classified

    at = (AOS + timedelta(days=3)).isoformat()
    assert main(["reliability", "report", "--at", at]) == 0
    report = capsys.readouterr().out
    assert "measured: 2 classified passes" in report
    assert "pass capture rate          1/2 = 50.0%" in report

    assert main(["reliability", "explain", "as_absent"]) == 0
    explained = capsys.readouterr().out
    assert "classification     station_unavailable" in explained
    assert '"heard_during_window": false' in explained
    assert '"state": "expired"' in explained


@pytest.mark.usefixtures("world")
def test_explaining_a_pass_not_yet_classified_says_why(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["reliability", "explain", "as_heard"]) == 1
    assert "not settled yet" in capsys.readouterr().err


def test_a_configuration_it_cannot_obey_is_refused_before_any_query(
    tmp_path: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "reliability.toml"
    config.write_text("[slo]\nwindow_days = 0\n", encoding="utf-8")

    assert main(["reliability", "--config", str(config), "report"]) == 1
    assert "window_days must be in" in capsys.readouterr().err
