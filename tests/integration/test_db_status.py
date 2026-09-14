"""``meridian db status`` against the real migrated test database.

tests/unit/test_cli_db.py pins every verdict. This proves the command reads the
revision through its own connection and finds the test database at head, which is
the state every other integration test already depends on.

Marked ``integration`` by the directory hook in ``tests/conftest.py``.

Reference: docs/DECISIONS.md D-111.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from meridian.cli_db import run_db

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_the_migrated_test_database_is_reported_up_to_date(
    database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.chdir(REPO_ROOT)

    result = run_db(argparse.Namespace(command="db", action="status"))

    assert result == 0
    assert "schema:            up to date" in capsys.readouterr().out
