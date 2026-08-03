"""0006_heartbeats

Applies sql/0006_heartbeats.sql. See deploy/migrations/_sql.py.
"""

from __future__ import annotations

from _sql import apply, not_supported

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    apply("0006_heartbeats")


def downgrade() -> None:
    not_supported()
