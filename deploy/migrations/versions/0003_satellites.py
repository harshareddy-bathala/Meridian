"""Revision 0003 - satellites, their transmitters and the element-set archive.

Applies sql/0003_satellites.sql. See deploy/migrations/_sql.py.
"""

from __future__ import annotations

from _sql import apply, not_supported

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    apply("0003_satellites")


def downgrade() -> None:
    not_supported()
