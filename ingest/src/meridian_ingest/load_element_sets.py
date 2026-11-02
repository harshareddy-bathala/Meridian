"""A catalogue's element sets into ``element_sets``, for tracked satellites only.

Split from :mod:`meridian_ingest.load` when Stage 34 brought element sets in
through ingest, so the archive and sample loads stay readable beside it. Called
inside :func:`~meridian_ingest.load.load_artefact`'s transaction: an artefact's
sets land with its record or not at all.

Reference: docs/DECISIONS.md D-320, D-325.
"""

from __future__ import annotations

from dataclasses import dataclass

from meridian.store.element_sets import NewElementSet, insert_element_set
from meridian.store.satellites import find_tracked_satellite_ids
from meridian.store.stations import Connection
from meridian_ingest.adapters.public import celestrak, satnogs_network
from meridian_ingest.normalise.records import NormalisedBatch
from meridian_ingest.raw_manifest import RawManifest

__all__ = ["ElementSetsApplied", "load_element_sets"]

_ELEMENT_SET_SOURCES = {
    **celestrak.ELEMENT_SET_SOURCES,
    **satnogs_network.ELEMENT_SET_SOURCES,
}
"""The ``element_sets.source`` each source's element sets load under."""


@dataclass(frozen=True, slots=True)
class ElementSetsApplied:
    """What an artefact's element sets came to: written, held, or not ours to load."""

    written: int = 0
    already_held: int = 0
    untracked: int = 0


def load_element_sets(
    conn: Connection, manifest: RawManifest, record_id: int, batch: NormalisedBatch
) -> ElementSetsApplied:
    """Store the sets of satellites the catalogue tracks, and count the rest.

    **Ingest never decides what pass generation propagates.** A catalogue
    publishes far more objects than this deployment tracks, and storing a set
    for one of them would need a ``satellites`` row the operator never wrote —
    the same argument that keeps archive receptions off ``satellites`` (D-139).
    So a set for an untracked object is counted and left in the raw store, from
    which a later load takes it if the catalogue ever adds the satellite
    (D-320).

    Each set carries the artefact it came from and the artefact's retrieval
    instant, not the instant of this load.
    """
    if not batch.element_sets:
        return ElementSetsApplied()
    tracked = find_tracked_satellite_ids(conn)
    written = already_held = untracked = 0
    for one in batch.element_sets:
        if one.satellite_key not in tracked:
            untracked += 1
            continue
        stored = insert_element_set(
            conn,
            NewElementSet(
                satellite_id=one.satellite_key,
                epoch=one.epoch,
                line1=one.line1,
                line2=one.line2,
                source=_ELEMENT_SET_SOURCES[manifest.provenance.source_id],
                ingest_record_id=record_id,
                retrieved_at=manifest.provenance.retrieved_at,
            ),
        )
        written += 1 if stored else 0
        already_held += 0 if stored else 1
    return ElementSetsApplied(written, already_held, untracked)
