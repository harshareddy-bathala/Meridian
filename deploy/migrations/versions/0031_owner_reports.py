"""Revision 0031 - owner reports.

Applies sql/0031_owner_reports.sql. See deploy/migrations/_sql.py.
"""

from __future__ import annotations

from _sql import apply, not_supported

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    apply("0031_owner_reports")


def downgrade() -> None:
    not_supported()
