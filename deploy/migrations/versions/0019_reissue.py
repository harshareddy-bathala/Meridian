"""Revision 0019 - an assignment can be revoked, and a pass decided again.

Applies sql/0019_reissue.sql. See deploy/migrations/_sql.py.
"""

from __future__ import annotations

from _sql import apply, not_supported

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    apply("0019_reissue")


def downgrade() -> None:
    not_supported()
