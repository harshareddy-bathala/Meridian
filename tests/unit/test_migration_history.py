"""The revision graph itself, checked without a database.

These are the failures that are cheap to prevent and expensive to meet in
production: two heads after a bad merge, a revision whose ``.sql`` file was never
added, a downgrade path that silently half-runs. None of them need Postgres to
detect, so none of them should wait for an integration run to be caught.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = REPO_ROOT / "deploy" / "migrations"
SQL_DIR = MIGRATIONS / "sql"


@pytest.fixture(scope="module")
def script() -> ScriptDirectory:
    config = Config(str(REPO_ROOT / "deploy" / "alembic.ini"))
    return ScriptDirectory.from_config(config)


def test_exactly_one_head(script: ScriptDirectory) -> None:
    """Two heads means `upgrade head` is ambiguous and Alembic refuses to run.

    It is produced by two branches each adding a migration off the same parent,
    which is the normal way a three-person team creates one.
    """
    assert len(script.get_heads()) == 1, f"expected one head, found {script.get_heads()}"


def test_history_is_linear_and_gapless(script: ScriptDirectory) -> None:
    """0001 → 0006, each pointing at its predecessor, no branches."""
    revisions = list(script.walk_revisions())
    identifiers = [revision.revision for revision in reversed(revisions)]
    assert identifiers == [f"{n:04d}" for n in range(1, len(identifiers) + 1)]

    for revision in revisions:
        assert not isinstance(revision.down_revision, tuple), (
            f"{revision.revision} is a merge revision; the history is meant to be linear"
        )


def test_every_revision_has_its_sql_file(script: ScriptDirectory) -> None:
    """D-019: a revision is a thin wrapper that executes its `.sql` file.

    A wrapper whose file is missing fails at `upgrade` time against a real
    database — which, for a migration, means it fails half way through a
    deployment rather than in CI.
    """
    modules = sorted(p.stem for p in (MIGRATIONS / "versions").glob("[0-9]*.py"))
    statements = sorted(p.stem for p in SQL_DIR.glob("*.sql"))
    assert modules == statements

    revisions = {revision.revision for revision in script.walk_revisions()}
    assert {name.split("_")[0] for name in modules} == revisions


def test_every_revisions_downgrade_is_refused_not_silently_skipped(
    script: ScriptDirectory,
) -> None:
    """GIT-WORKFLOW.md rule 9: migrations are forward-only.

    The failure this prevents is a `downgrade()` that is a no-op `pass`. Alembic
    reports success, the version table moves backwards, and the schema does not —
    which is worse than an error, because nothing tells you.

    **Every revision's own wrapper is called**, not the shared helper it is meant
    to delegate to. Testing ``not_supported()`` directly asserts that the helper
    raises, which was never in doubt; it says nothing about whether revision 0004
    remembered to call it. A revision written by copying one that predates the
    helper is exactly how a bare ``pass`` arrives, and that is the one this has to
    catch.
    """
    revisions = sorted(script.walk_revisions(), key=lambda revision: revision.revision)
    assert revisions, "no revisions found; the rest of this test would vacuously pass"

    for revision in revisions:
        downgrade = revision.module.downgrade
        with pytest.raises(NotImplementedError, match="forward-only"):
            downgrade()


def test_every_revisions_upgrade_applies_its_own_sql_file(script: ScriptDirectory) -> None:
    """A wrapper that applies the wrong file is silent until it runs.

    ``upgrade()`` is called against a recording stub rather than a database, so
    the check is which file each revision names, not what the SQL does. A copied
    wrapper still pointing at its parent's ``.sql`` would leave one table missing
    and one applied twice, and the revision graph in the tests above cannot see
    it — the file exists, and it belongs to a real revision.
    """
    from unittest import mock

    for revision in sorted(script.walk_revisions(), key=lambda r: r.revision):
        module = revision.module
        with mock.patch.object(module, "apply") as apply:
            module.upgrade()
        apply.assert_called_once()
        (name,) = apply.call_args.args
        assert name.split("_")[0] == revision.revision, (
            f"revision {revision.revision} applies {name!r}"
        )
        assert (SQL_DIR / f"{name}.sql").is_file()


def test_no_sql_file_drops_or_truncates() -> None:
    """Migrations add; they do not destroy.

    A `drop table` or `truncate` reaching the observation lineage is
    unrecoverable, and DATA-MODEL.md's conventions say soft delete only. This
    reads the SQL rather than trusting the convention.
    """
    forbidden = ("drop table", "drop column", "truncate", "delete from")
    for path in sorted(SQL_DIR.glob("*.sql")):
        body = path.read_text(encoding="utf-8").lower()
        for phrase in forbidden:
            assert phrase not in body, f"{path.name} contains {phrase!r}"
