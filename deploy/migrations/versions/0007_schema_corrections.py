"""Revision 0007 - constraints that were missing, wrong, or narrower on a twin.

Applies sql/0007_schema_corrections.sql. See deploy/migrations/_sql.py.
"""

from __future__ import annotations

from _sql import apply, not_supported

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    apply("0007_schema_corrections")


def downgrade() -> None:
    not_supported()
