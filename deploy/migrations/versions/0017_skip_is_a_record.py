"""Revision 0017 - a skipped decision is a record, never an assignment.

Applies sql/0017_skip_is_a_record.sql. See deploy/migrations/_sql.py.
"""

from __future__ import annotations

from _sql import apply, not_supported

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    apply("0017_skip_is_a_record")


def downgrade() -> None:
    not_supported()
