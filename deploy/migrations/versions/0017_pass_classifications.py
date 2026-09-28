"""Revision 0017 - what happened to each settled, scheduled pass.

Applies sql/0017_pass_classifications.sql. See deploy/migrations/_sql.py.
"""

from __future__ import annotations

from _sql import apply, not_supported

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    apply("0017_pass_classifications")


def downgrade() -> None:
    not_supported()
