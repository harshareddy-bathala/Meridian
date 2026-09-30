"""Revision 0026 - a transmitter's nominal frame interval.

Applies sql/0026_transmitter_frame_interval.sql. See deploy/migrations/_sql.py.
"""

from __future__ import annotations

from _sql import apply, not_supported

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    apply("0026_transmitter_frame_interval")


def downgrade() -> None:
    not_supported()
