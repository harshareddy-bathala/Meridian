"""Revision 0002 - invite tokens, stations and station capabilities.

Applies sql/0002_stations.sql. See deploy/migrations/_sql.py.
"""

from __future__ import annotations

from _sql import apply, not_supported

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    apply("0002_stations")


def downgrade() -> None:
    not_supported()
