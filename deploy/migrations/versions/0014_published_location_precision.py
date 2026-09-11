"""Revision 0014 - a station declares how precisely its location is published.

Applies sql/0014_published_location_precision.sql. See deploy/migrations/_sql.py.
"""

from __future__ import annotations

from _sql import apply, not_supported

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    apply("0014_published_location_precision")


def downgrade() -> None:
    not_supported()
