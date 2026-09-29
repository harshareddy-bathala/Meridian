"""Revision 0021 - published environmental and space-weather values.

Applies sql/0021_environment_samples.sql. See deploy/migrations/_sql.py.
"""

from __future__ import annotations

from _sql import apply, not_supported

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    apply("0021_environment_samples")


def downgrade() -> None:
    not_supported()
