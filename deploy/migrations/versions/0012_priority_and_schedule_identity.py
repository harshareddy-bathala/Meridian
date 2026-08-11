"""Revision 0012 - operator priority has a home, and a schedule can be re-run.

Applies sql/0012_priority_and_schedule_identity.sql. See deploy/migrations/_sql.py.
"""

from __future__ import annotations

from _sql import apply, not_supported

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    apply("0012_priority_and_schedule_identity")


def downgrade() -> None:
    not_supported()
