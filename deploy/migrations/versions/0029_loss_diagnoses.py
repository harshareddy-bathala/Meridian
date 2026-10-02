"""Revision 0029 - loss diagnoses.

Applies sql/0029_loss_diagnoses.sql. See deploy/migrations/_sql.py.
"""

from __future__ import annotations

from _sql import apply, not_supported

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    apply("0029_loss_diagnoses")


def downgrade() -> None:
    not_supported()
