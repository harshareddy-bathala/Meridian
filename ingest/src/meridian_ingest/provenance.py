"""What an adapter declares about one retrieved artefact, before it is stored.

Six fields, and three optional ones that describe what the artefact *covers*.
They become a manifest beside the bytes in the raw store and a row in
``ingest_records``, so this is the one place the rules about them live.

**Construction is the check.** An incomplete provenance cannot be built, which
is a stronger statement than a validator somebody has to remember to call: the
raw store takes a :class:`Provenance`, so bytes with no recorded origin are not
merely refused, they are unrepresentable. D-134 wants a source's terms written
down before its first retrieval, and the same reasoning applies one level
further in — an artefact whose origin nobody recorded is an artefact no
evaluation can cite.

**What is *not* here is as deliberate.** The digest and the byte count are
measured by the store from the bytes it actually wrote, never declared. A
length an adapter reports is a claim about a download, and the one thing this
layer exists to hold is evidence.

Reference: docs/DECISIONS.md D-133, D-134, D-140, D-141.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

__all__ = [
    "PAYLOAD_KINDS",
    "SOURCE_ID",
    "IncompleteProvenanceError",
    "Provenance",
    "check_interval",
]

SOURCE_ID = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
"""The pattern ``ingest_sources.source_id`` is CHECKed against in 0016.

Repeated here rather than read from the database, because the store writes
directories before any row exists and the whole point of a source id is that it
is *ours*: it is the only remote-ish string that ever becomes a path element,
and it is ours precisely so that it is safe to be one.
"""

PAYLOAD_KINDS = ("data", "tile")
"""``tile`` marks a styled raster meant to be displayed.

Carried from the fetch so a normaliser can refuse one (D-133): a tile is a
picture of a measurement, and sampling its pixels for a number invents
precision the tile never had.
"""


class IncompleteProvenanceError(ValueError):
    """An artefact whose origin is not fully recorded.

    Raised at construction, so the refusal happens before a socket is opened
    rather than after bytes are on disk with nothing to say where they came
    from.
    """


@dataclass(frozen=True, slots=True)
class Provenance:
    """Where one artefact came from, as the adapter that fetched it declares.

    Raises:
        IncompleteProvenanceError: Any field is missing, blank, out of its
            vocabulary, or — for ``retrieved_at`` — naive.
    """

    source_id: str
    """Ours, pattern-checked, and the only part of this that becomes a path."""

    original_identifier: str
    """The source's own identifier for the artefact, verbatim.

    It lives in the manifest and in ``ingest_records``, and **never** in a path
    (D-141). Nothing here constrains its shape, because constraining it would
    be the beginning of trusting it.
    """

    source_version: str
    """The source's own version of this artefact, usually a returned header.

    Non-empty: an artefact of unknown vintage cannot be compared against its
    successor, so a retrieval that cannot say which version it took is refused
    before anything is published.
    """

    payload_kind: str
    """One of :data:`PAYLOAD_KINDS`."""

    retrieved_at: datetime
    """When *we* fetched it, timezone-aware.

    Not defaulted anywhere in this path. The manifest written beside the bytes
    holds the truth, and a load that runs days later reads it rather than
    inventing one — which is also why ``ingest_records.retrieved_at`` has no
    column default.
    """

    media_type: str
    """What the source said the bytes are. Stored, never used to pick a parser."""

    valid_from: datetime | None = None
    valid_to: datetime | None = None
    """The interval the artefact *describes*, which is not when we fetched it.

    A feature lookup selects on what an artefact describes and on when it was
    published, never on when we happened to take it (D-131). Both None where
    the artefact describes no interval.
    """

    spatial_extent: dict[str, float] | None = None
    """The ground it covers, as a bounding box, or None where it covers none."""

    def __post_init__(self) -> None:
        """Refuse construction unless every declared field is present."""
        for field in ("original_identifier", "source_version", "media_type"):
            if not str(getattr(self, field)).strip():
                message = f"{field} is empty; an artefact needs one to be citable"
                raise IncompleteProvenanceError(message)
        if not SOURCE_ID.fullmatch(self.source_id):
            message = (
                f"source_id {self.source_id!r} is not ours to use as a directory "
                f"name: it must match {SOURCE_ID.pattern}"
            )
            raise IncompleteProvenanceError(message)
        if self.payload_kind not in PAYLOAD_KINDS:
            message = (
                f"payload_kind {self.payload_kind!r} is not one of {PAYLOAD_KINDS}"
            )
            raise IncompleteProvenanceError(message)
        if self.retrieved_at.tzinfo is None:
            message = "retrieved_at is naive; it is read back as UTC and must say so"
            raise IncompleteProvenanceError(message)
        check_interval(self.valid_from, self.valid_to)


def check_interval(valid_from: datetime | None, valid_to: datetime | None) -> None:
    """The two coverage instants, held to what 0016's CHECKs also hold them to.

    Args:
        valid_from: The start of what an artefact describes, or None.
        valid_to: The end, or None.

    Raises:
        IncompleteProvenanceError: The interval has an end and no start, either
            instant is naive, or it runs backwards.

    Note:
        Public because an adapter plans coverage before it fetches, so
        :class:`~meridian_ingest.retrieval.RemoteArtefact` holds the same two
        instants and must hold them to the same rules. Checked here as well as
        in the database because the raw store writes a manifest before any row
        exists, and a manifest the loader will later refuse is a directory
        nobody can do anything with.
    """
    if valid_to is not None and valid_from is None:
        message = "valid_to without valid_from is an interval with no start"
        raise IncompleteProvenanceError(message)
    for name, instant in (("valid_from", valid_from), ("valid_to", valid_to)):
        if instant is not None and instant.tzinfo is None:
            message = f"{name} is naive; every stored instant is UTC"
            raise IncompleteProvenanceError(message)
    if valid_from is not None and valid_to is not None and valid_to < valid_from:
        message = f"valid_to {valid_to} precedes valid_from {valid_from}"
        raise IncompleteProvenanceError(message)
