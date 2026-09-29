"""Sources whose artefacts can only be named after reading a listing of them.

Most sources are asked for by a URL an adapter can write down from the request
alone. Some archives name each granule with its own production time, so the
name exists only in the archive's directory listing. For those, planning is two
steps: the adapter plans the listing, the fetch stores it like any artefact,
and :meth:`ListingAdapter.expand` reads the stored listing and names the
granules to fetch next.

**Expansion reads the stored listing, not the network.** It takes the listing
as a :class:`~meridian_ingest.raw_store.StoredArtefact`, so which granules were
asked for is decided by bytes we hold and can show, and the listing itself is
provenance for the choice (D-141, D-142).

Reference: docs/DECISIONS.md D-141, D-142, D-226.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from meridian_ingest.adapters.protocol import FetchRequest
from meridian_ingest.raw_store import StoredArtefact
from meridian_ingest.retrieval import RemoteArtefact

__all__ = ["LISTING_SUFFIX", "ListingAdapter"]

LISTING_SUFFIX = "/listing"
"""Ends the ``original_identifier`` of every listing an adapter plans, so a
normaliser can recognise one by the identifier we gave it, never by its bytes."""


@runtime_checkable
class ListingAdapter(Protocol):
    """An adapter that names its granules from a listing it planned first."""

    def expand(
        self, listing: StoredArtefact, request: FetchRequest
    ) -> tuple[RemoteArtefact, ...]:
        """The granules a stored listing names that the request wants.

        Args:
            listing: The listing, as the raw store holds it.
            request: The same request the listing was planned from.

        Returns:
            Names, not bytes — as :meth:`Adapter.plan` returns.
        """
        ...
