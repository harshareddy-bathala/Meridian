"""0005_observations

Applies sql/0005_observations.sql. See deploy/migrations/_sql.py.
"""

from __future__ import annotations

from _sql import apply, not_supported

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    apply("0005_observations")


def downgrade() -> None:
    not_supported()
