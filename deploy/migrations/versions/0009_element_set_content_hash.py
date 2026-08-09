"""Revision 0009 - element sets are identified by their contents, not their epoch.

Applies sql/0009_element_set_content_hash.sql. See deploy/migrations/_sql.py.
"""

from __future__ import annotations

from _sql import apply, not_supported

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    apply("0009_element_set_content_hash")


def downgrade() -> None:
    not_supported()
