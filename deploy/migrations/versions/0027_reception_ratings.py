"""Revision 0027 - reception ratings.

Applies sql/0027_reception_ratings.sql. See deploy/migrations/_sql.py.
"""

from __future__ import annotations

from _sql import apply, not_supported

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    apply("0027_reception_ratings")


def downgrade() -> None:
    not_supported()
