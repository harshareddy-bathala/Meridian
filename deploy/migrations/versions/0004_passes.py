"""Revision 0004 - computed passes and the assignments issued against them.

Applies sql/0004_passes.sql. See deploy/migrations/_sql.py.
"""

from __future__ import annotations

from _sql import apply, not_supported

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    apply("0004_passes")


def downgrade() -> None:
    not_supported()
