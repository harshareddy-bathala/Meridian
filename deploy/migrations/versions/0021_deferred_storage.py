"""Revision 0021 - the four deferred tables: noise, profiles, products.

Applies sql/0021_deferred_storage.sql. See deploy/migrations/_sql.py.
"""

from __future__ import annotations

from _sql import apply, not_supported

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    apply("0021_deferred_storage")


def downgrade() -> None:
    not_supported()
