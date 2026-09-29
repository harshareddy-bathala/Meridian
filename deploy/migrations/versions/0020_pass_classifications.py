"""Revision 0020 - what happened to each settled, scheduled pass.

Applies sql/0020_pass_classifications.sql. See deploy/migrations/_sql.py.
"""

from __future__ import annotations

from _sql import apply, not_supported

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    apply("0020_pass_classifications")


def downgrade() -> None:
    not_supported()
