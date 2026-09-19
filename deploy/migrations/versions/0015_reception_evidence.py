"""Revision 0015 - MSP 0.3's reception evidence on observations.

Applies sql/0015_reception_evidence.sql. See deploy/migrations/_sql.py.
"""

from __future__ import annotations

from _sql import apply, not_supported

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    apply("0015_reception_evidence")


def downgrade() -> None:
    not_supported()
