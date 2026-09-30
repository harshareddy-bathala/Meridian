"""Which archive sources a raw snapshot holds rows from, and their terms.

The manifest carries every cited source's licence, terms and attribution, so a
dataset says with it whether we were allowed to use what it holds (D-134). Read
inside the export's transaction, over the same scope as
:mod:`meridian.store.snapshot_reads`, from whose scope queries it takes the set
of cited records.

Reference: docs/DECISIONS.md D-134, D-139, D-143.
"""

from __future__ import annotations

from dataclasses import dataclass

from meridian.store.snapshot_reads import _CITED_RECORDS, SnapshotScope
from meridian.store.stations import Connection

__all__ = ["SourceTerms", "read_source_terms"]


@dataclass(frozen=True, slots=True)
class SourceTerms:
    """One archive source a snapshot holds rows from, and the terms they came under."""

    source_id: str
    licence: str
    terms_url: str
    attribution_entry: str
    records: int
    """Artefacts from this source that the snapshot's archive receptions cite."""


def read_source_terms(conn: Connection, scope: SnapshotScope) -> list[SourceTerms]:
    """The terms of every source the snapshot holds receptions or values from.

    Args:
        conn: A connection inside the export's transaction.
        scope: The snapshot's interval.

    Returns:
        One entry per source, ordered by ``source_id``, for the manifest — so a
        dataset carries "were we allowed to use this" with it (D-134).
    """
    with conn.cursor() as cur:
        cur.execute(
            "select source_id, licence, terms_url, attribution_entry,"
            f" count(*) from ingest_provenance where record_id in ({_CITED_RECORDS})"
            " group by source_id, licence, terms_url, attribution_entry"
            " order by source_id",
            {"since": scope.since, "as_of": scope.as_of},
        )
        return [_source_terms(row) for row in cur.fetchall()]


def _source_terms(row: tuple[object, ...]) -> SourceTerms:
    """One grouped row, with every column the view declares ``not null`` checked."""
    source_id, licence, terms_url, attribution_entry, records = row
    texts = (source_id, licence, terms_url, attribution_entry)
    if not all(isinstance(one, str) for one in texts) or not isinstance(records, int):
        message = f"ingest_provenance returned an unexpected row: {row!r}"
        raise TypeError(message)
    return SourceTerms(
        source_id=str(source_id),
        licence=str(licence),
        terms_url=str(terms_url),
        attribution_entry=str(attribution_entry),
        records=records,
    )
