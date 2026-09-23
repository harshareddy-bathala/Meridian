"""Revision 0016 - ingest provenance, and archive receptions kept apart.

Applies sql/0016_archive_ingest.sql. See deploy/migrations/_sql.py.
"""

from __future__ import annotations

from _sql import apply, not_supported

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    apply("0016_archive_ingest")


def downgrade() -> None:
    not_supported()
