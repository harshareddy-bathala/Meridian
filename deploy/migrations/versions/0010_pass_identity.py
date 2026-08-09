"""Revision 0010 - a computed pass has an identity, so regenerating one is free.

Applies sql/0010_pass_identity.sql. See deploy/migrations/_sql.py.
"""

from __future__ import annotations

from _sql import apply, not_supported

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    apply("0010_pass_identity")


def downgrade() -> None:
    not_supported()
