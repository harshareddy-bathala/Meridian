"""Imagery that is only ever displayed: the two tile sources' shared half.

A tile has been through a colour map, a projection, resampling and lossy
compression chosen for legibility, and a pixel read back is a fact about the
rendering rather than the scene (D-133). So a tile source's normaliser has one
behaviour, which is to refuse — and the loader skips a tile before any
normaliser is asked, so this refusal is the second of two locks, not the only
one.

Reference: docs/DECISIONS.md D-133.
"""

from __future__ import annotations

from meridian_ingest.adapters.protocol import TileIsNotAQuantityError, refuse_a_tile
from meridian_ingest.normalise.records import NormalisedBatch
from meridian_ingest.raw_store import StoredArtefact

__all__ = ["DisplayOnlyNormaliser"]


class DisplayOnlyNormaliser:
    """The normaliser of a source that publishes nothing we may read a number from.

    Args:
        transformation_version: Recorded for form's sake; nothing is produced
            under it.
    """

    def __init__(self, transformation_version: str) -> None:
        """Hold the version. There is nothing else to hold."""
        self.transformation_version = transformation_version

    def normalise(self, artefact: StoredArtefact) -> NormalisedBatch:
        """Refuse, always.

        Raises:
            TileIsNotAQuantityError: Every time. An artefact from a display-only
                source mislabelled as ``data`` is refused by name too, because
                what may be derived is decided by the source, not by a label an
                adapter could get wrong.
        """
        refuse_a_tile(artefact.manifest.provenance)
        message = (
            f"{artefact.raw_path} comes from a display-only source; no number is "
            "derived from it (D-133, D-220)"
        )
        raise TileIsNotAQuantityError(message)
