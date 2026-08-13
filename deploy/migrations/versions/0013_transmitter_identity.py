"""Revision 0013 - a transmitter is identified by what it transmits.

Applies sql/0013_transmitter_identity.sql. See deploy/migrations/_sql.py.
"""

from __future__ import annotations

from _sql import apply, not_supported

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    apply("0013_transmitter_identity")


def downgrade() -> None:
    not_supported()
