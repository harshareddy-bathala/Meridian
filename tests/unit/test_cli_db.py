"""``meridian db status`` tells behind from ahead, and says what to run.

The comparison is pure, so every verdict is pinned here without a database; the
one run against a real migrated schema is tests/integration/test_db_status.py.
What matters most is the third verdict: a database migrated by a newer image is
not "behind", and telling the operator to run ``migrate`` would send them the
wrong way.

Marked as a unit test by living in ``tests/unit``: no network, no database. The
scripts are read from ``deploy/alembic.ini``, which is a file in the checkout.

Reference: docs/DECISIONS.md D-109, D-111.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from meridian.cli_db import judge_schema
from meridian.store.schema_revision import find_head_revision, find_known_revisions

REPO_ROOT = Path(__file__).resolve().parents[2]
KNOWN = frozenset({"0001", "0002", "0003"})


def test_the_head_revision_is_up_to_date() -> None:
    verdict = judge_schema("0003", "0003", KNOWN)

    assert verdict.up_to_date
    assert verdict.summary == "up to date"


@pytest.mark.parametrize(
    ("current", "expected"),
    [
        (None, "no migrations applied"),
        ("0002", "behind the code"),
        ("0009", "a newer image migrated it"),
    ],
)
def test_every_other_revision_is_not_up_to_date_and_says_why(
    current: str | None, expected: str
) -> None:
    verdict = judge_schema(current, "0003", KNOWN)

    assert not verdict.up_to_date
    assert expected in verdict.summary


def test_only_a_database_behind_the_code_is_told_to_migrate() -> None:
    """``migrate`` from an older image cannot undo a newer image's migration."""
    assert "migrate" in judge_schema("0002", "0003", KNOWN).summary
    assert "run --rm migrate" not in judge_schema("0009", "0003", KNOWN).summary


def test_the_scripts_know_their_own_head(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(REPO_ROOT)

    head = find_head_revision()

    assert head is not None
    assert head in find_known_revisions()


def test_no_configuration_means_no_known_revisions(tmp_path: Path) -> None:
    assert find_known_revisions(tmp_path / "missing.ini") == frozenset()
