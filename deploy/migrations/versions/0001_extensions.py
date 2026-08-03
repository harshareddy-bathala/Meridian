"""0001_extensions

Applies sql/0001_extensions.sql. See deploy/migrations/_sql.py.
"""

from __future__ import annotations

from _sql import apply, not_supported

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    apply("0001_extensions")


def downgrade() -> None:
    not_supported()
