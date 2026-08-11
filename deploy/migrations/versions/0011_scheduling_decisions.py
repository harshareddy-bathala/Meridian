"""Revision 0011 - a scheduling decision records its score and what displaced it.

Applies sql/0011_scheduling_decisions.sql. See deploy/migrations/_sql.py.
"""

from __future__ import annotations

from _sql import apply, not_supported

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    apply("0011_scheduling_decisions")


def downgrade() -> None:
    not_supported()
