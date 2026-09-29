"""Revision 0018 - scheduler runs, and an explanation on every decision.

Applies sql/0018_schedule_runs.sql. See deploy/migrations/_sql.py.
"""

from __future__ import annotations

from _sql import apply, not_supported

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    apply("0018_schedule_runs")


def downgrade() -> None:
    not_supported()
