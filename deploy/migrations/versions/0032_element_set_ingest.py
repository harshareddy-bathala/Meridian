"""Revision 0032 - element sets arrive through ingest.

Applies sql/0032_element_set_ingest.sql. See deploy/migrations/_sql.py.
"""

from __future__ import annotations

from _sql import apply, not_supported

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    apply("0032_element_set_ingest")


def downgrade() -> None:
    not_supported()
