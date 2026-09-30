"""Revision 0025 - every revocation and reinstatement, kept after it is undone.

Applies sql/0025_assignment_revocations.sql. See deploy/migrations/_sql.py.
"""

from __future__ import annotations

from _sql import apply, not_supported

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    apply("0025_assignment_revocations")


def downgrade() -> None:
    not_supported()
