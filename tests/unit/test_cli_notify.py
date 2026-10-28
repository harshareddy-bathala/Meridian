"""``meridian notify`` — what it prints, and its one action with no database.

Marked as a unit test by living in ``tests/unit``. ``list`` and ``regenerate``
read the database and are exercised by the gate,
``tests/integration/test_report_gate.py``; here, the lines they print and
``test`` by the sink, which needs nothing but a directory.

Reference: docs/DECISIONS.md D-300, D-302.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from meridian.cli import main
from meridian.cli_notify import delivery_lines
from meridian.store.report_deliveries import StoredDelivery

QUEUED = datetime(2026, 10, 14, 12, tzinfo=UTC)


def stored(**changes: object) -> StoredDelivery:
    values: dict[str, object] = {
        "id": 7,
        "station_id": "st_001",
        "kind": "pass",
        "assignment_id": "a-001",
        "period_start": None,
        "channel": "email",
        "template_version": "pass-1",
        "inputs_json": {},
        "inputs_sha256": bytes(32),
        "content_sha256": bytes.fromhex("ab" * 32),
        "queued_at": QUEUED,
        "simulated": False,
        "attempts": 2,
        "state": "retrying",
        "last_error": "timeout",
    } | changes
    return StoredDelivery(**values)  # type: ignore[arg-type]


def test_a_report_is_one_line_with_its_state_and_last_error() -> None:
    [line] = delivery_lines([stored()])

    assert line == (
        "7 pass a-001 st_001 by email: retrying after 2 attempts, last error"
        " timeout  [pass-1, content abababababab]"
    )


def test_a_week_and_a_simulated_station_say_so() -> None:
    [line] = delivery_lines(
        [
            stored(
                kind="weekly",
                assignment_id=None,
                period_start=datetime(2026, 10, 12, tzinfo=UTC),
                simulated=True,
                channel="sink",
                state="sent",
                attempts=1,
                last_error=None,
            )
        ]
    )

    assert line.startswith(
        "7 weekly week of 2026-10-12 st_001 (simulated) by sink: sent"
    )
    assert "last error" not in line


def test_no_reports_says_so() -> None:
    assert delivery_lines([]) == ["no reports"]


def test_a_channel_is_tested_by_the_sink_with_no_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "notifications.toml"
    config.write_text(f'[sink]\ndirectory = "{tmp_path / "sink"}"\n', encoding="utf-8")
    monkeypatch.setenv("MERIDIAN_NOTIFICATIONS_CONFIG", str(config))

    assert main(["notify", "test", "--channel", "sink", "--to", "team"]) == 0

    [written] = (tmp_path / "sink").iterdir()
    assert written.name == "0.txt"
    assert "not a report" in written.read_text(encoding="utf-8")
    assert "sent a test message by sink" in capsys.readouterr().out


def test_a_channel_with_no_secret_is_refused(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("MERIDIAN_NOTIFICATIONS_CONFIG", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN_FILE", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    assert main(["notify", "test", "--channel", "telegram", "--to", "team"]) == 1
    assert "no secret or settings" in capsys.readouterr().err
