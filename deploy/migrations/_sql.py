"""Loader shared by every revision.

Each revision is a thin wrapper that executes its ``.sql`` file. The SQL is the
schema; Alembic only orders it and records what has been applied (D-019).
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

SQL_DIR = Path(__file__).resolve().parent / "sql"


def apply(name: str) -> None:
    """Execute ``sql/<name>.sql`` as one statement batch."""
    path = SQL_DIR / f"{name}.sql"
    op.execute(path.read_text(encoding="utf-8"))


def not_supported() -> None:
    """Downgrades are not implemented.

    GIT-WORKFLOW.md rule 9: migrations are never edited once merged, and the fix
    for a bad migration is a new one. A downgrade path here would be dead code
    that has never been run and therefore lies about being tested.
    """
    raise NotImplementedError(
        "Migrations are forward-only. Fix forward with a new migration "
        "(GIT-WORKFLOW.md rule 9)."
    )
