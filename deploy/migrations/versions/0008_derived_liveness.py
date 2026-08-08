"""Revision 0008 - drop stored liveness; it is derived on read.

Applies sql/0008_derived_liveness.sql. See deploy/migrations/_sql.py.
"""

from __future__ import annotations

from _sql import apply, not_supported

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    apply("0008_derived_liveness")


def downgrade() -> None:
    not_supported()
