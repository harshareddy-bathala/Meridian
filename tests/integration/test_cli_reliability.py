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


@pytest.mark.usefixtures("world")
def test_faults_publish_seals_what_it_read_so_a_report_judges_it_again(
    tmp_path: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    """Stage 22's fault runs, from the live record (D-240).

    The command reads the evidence from the database and seals it; reading the
    sealed run back and judging it again, with no database, reaches the
    verdicts the command printed.
    """
    import json

    from meridian.datasets.fault_runs import read_fault_run
    from meridian.reliability.fault_record import verdict_rows
    from meridian.reliability.faults import judge_gathered

    def line(event: str, kind: str, target: str, at: datetime, **more: str) -> str:
        stamp = at.isoformat().replace("+00:00", "Z")
        fields = {"ledger": 1, "event": event, "run_id": "r", "kind": kind}
        return json.dumps(fields | {"target": target, "at": stamp} | more)

    ledger = tmp_path / "faults.jsonl"
    ledger.write_text(
        "\n".join(
            [
                line("open", "network_down", "station:1", AOS, station_id="st_a"),
                line("close", "network_down", "station:1", AOS + timedelta(minutes=9)),
                line("open", "api_paused", "platform:api", AOS),
                line("close", "api_paused", "platform:api", AOS + timedelta(minutes=2)),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    root = tmp_path / "datasets"

    code = main(
        [
            "reliability",
            "faults",
            "--ledger",
            str(ledger),
            "--publish",
            "--root",
            str(root),
        ]
    )
    out = capsys.readouterr().out

    assert code in {0, 1}
    assert "fault run: " in out
    path = next((root / "faults").iterdir())
    run = read_fault_run(path)
    again = verdict_rows([judge_gathered(one) for one in run.gathered])
    assert json.loads(json.dumps(again)) == list(run.verdicts)
    assert run.directory.manifest.kind == "fault_run"
    assert run.directory.manifest.counts["faults"] == 2
    assert run.directory.manifest.schema_revision != "unknown"


def _ledger_file(tmp_path: Any) -> Any:
    import json

    stamp = AOS.isoformat().replace("+00:00", "Z")
    closed = (AOS + timedelta(minutes=2)).isoformat().replace("+00:00", "Z")
    lines = [
        {"event": "open", "at": stamp},
        {"event": "close", "at": closed},
    ]
    ledger = tmp_path / "faults.jsonl"
    ledger.write_text(
        "".join(
            json.dumps(
                {"ledger": 1, "run_id": "r", "kind": "api_paused"}
                | {"target": "platform:api"}
                | one
            )
            + "\n"
            for one in lines
        ),
        encoding="utf-8",
    )
    return ledger


def test_a_long_run_s_record_is_sealed_with_its_faults(
    tmp_path: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    """D-257: the tool's own judgement, under the same hash as the verdicts.

    What the command prints is what ``deploy/tools/long_run.py`` reads the
    sealed run's name and hash back from.
    """
    import json
    import re

    from meridian.datasets.fault_runs import read_fault_run

    record = tmp_path / "long_run.json"
    record.write_text(
        json.dumps(
            {
                "format": "meridian-long-run/2",
                "started": AOS.isoformat(),
                "ended": (AOS + timedelta(hours=72)).isoformat(),
                "passed": True,
                "failures": [],
                "seed": 4471,
            }
        ),
        encoding="utf-8",
    )
    root = tmp_path / "datasets"
    arguments = ["reliability", "faults", "--ledger", str(_ledger_file(tmp_path))]

    main([*arguments, "--publish", "--root", str(root), "--run-record", str(record)])
    out = capsys.readouterr().out

    assert re.search(r"^fault run: \S*/([0-9a-f]{12}) ", out, re.M)
    assert re.search(r"^\s+hash\s+([0-9a-f]{64})\s*$", out, re.M)
    run = read_fault_run(next((root / "faults").iterdir()))
    assert run.record is not None
    assert (run.record.seed, run.record.hours) == (4471, 72.0)


def test_a_record_without_publish_or_that_is_not_one_is_refused(
    tmp_path: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = str(_ledger_file(tmp_path))
    record = tmp_path / "long_run.json"
    record.write_text('{"format": "meridian-long-run/2"}', encoding="utf-8")
    root = str(tmp_path / "datasets")

    assert (
        main(["reliability", "faults", "--ledger", ledger, "--run-record", str(record)])
        == 1
    )
    assert "give --publish" in capsys.readouterr().err
    refused = ["--publish", "--root", root, "--run-record", str(record)]
    assert main(["reliability", "faults", "--ledger", ledger, *refused]) == 1
    assert "failures" in capsys.readouterr().err
    assert not (tmp_path / "datasets" / "faults").exists()
