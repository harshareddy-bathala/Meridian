"""Revision 0030 - the receive-chain watch.

Applies sql/0030_receive_chain_watch.sql. See deploy/migrations/_sql.py.
"""

from __future__ import annotations

from _sql import apply, not_supported

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    apply("0030_receive_chain_watch")


def downgrade() -> None:
    not_supported()
