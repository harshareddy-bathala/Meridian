"""Revision 0028 - reception verdicts.

Applies sql/0028_reception_verdicts.sql. See deploy/migrations/_sql.py.
"""

from __future__ import annotations

from _sql import apply, not_supported

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    apply("0028_reception_verdicts")


def downgrade() -> None:
    not_supported()
