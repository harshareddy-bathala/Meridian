"""Revision 0021 - two views, and heartbeats summarised by the hour.

Applies sql/0021_views_and_heartbeat_aggregate.sql. See deploy/migrations/_sql.py.
"""

from __future__ import annotations

from _sql import apply, not_supported

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    apply("0021_views_and_heartbeat_aggregate")


def downgrade() -> None:
    not_supported()
