"""Revision 0022 - areas of interest, and the regional alerts raised about them.

Applies sql/0022_regions.sql. See deploy/migrations/_sql.py.
"""

from __future__ import annotations

from _sql import apply, not_supported

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    apply("0022_regions")


def downgrade() -> None:
    not_supported()
