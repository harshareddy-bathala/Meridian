"""The two protocols every source is reached through, and what they promise.

An :class:`Adapter` describes a source and decides which artefacts to ask for.
A :class:`Normaliser` turns one stored artefact into rows. They are separate
types, and the separation is enforced by the signatures rather than by a note:

* :meth:`Adapter.plan` returns names, not bytes. What turns a name into bytes
  is a :class:`~meridian_ingest.retrieval.Retriever` supplied from outside, so
  every adapter is exercisable against fixtures with nothing reachable.
* :meth:`Normaliser.normalise` takes a
  :class:`~meridian_ingest.raw_store.StoredArtefact` and nothing else — no
  retriever, no adapter, no URL, no clock. **There is no argument through which
  it could fetch anything**, which is what makes Stage 14's completion gate
  provable instead of asserted (D-142).

**A source cannot be described without its terms.** :class:`SourceDescriptor`
refuses construction without a licence, an https terms URL and the
``ATTRIBUTION.md`` entry it was recorded under — D-134 in type form, saying the
same thing ``ingest_sources``' NOT NULL columns say, one layer earlier. The
point of both is that no record can exist that arrived under terms nobody wrote
down.

Reference: docs/DECISIONS.md D-132, D-133, D-134, D-140, D-142.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from meridian_ingest.normalise.records import NormalisedBatch
from meridian_ingest.provenance import SOURCE_ID, Provenance
from meridian_ingest.raw_store import StoredArtefact
from meridian_ingest.retrieval import RemoteArtefact, RetrievedArtefact

__all__ = [
    "ACCESS_CONSTRAINTS",
    "SOURCE_CLASSES",
    "Adapter",
    "FetchRequest",
    "Normaliser",
    "SourceDescriptor",
    "TermsNotRecordedError",
    "TileIsNotAQuantityError",
    "refuse_a_tile",
    "require_source_version",
]

SOURCE_CLASSES = (
    "archive_receptions",
    "space_weather",
    "atmospheric",
    "imagery",
    "regional_product",
)
"""Classes, not vendors, as ``ATTRIBUTION.md`` names them (D-132).

The same enumeration ``ingest_sources.source_class`` is CHECKed against, so
Stage 31's sources describe themselves in the vocabulary Stage 14 established
rather than a second one.
"""

ACCESS_CONSTRAINTS = ("none", "key_counted", "registration")
"""How a source gates access. ``key_counted`` is the one that makes a request
budget an obligation rather than a courtesy."""


class TermsNotRecordedError(ValueError):
    """A source described without the terms it publishes under.

    Raised at construction, before a socket could be opened. D-134 asks for the
    attribution entry to land *before* the first retrieval; this is what stops
    "before" meaning "shortly afterwards, probably".
    """


class TileIsNotAQuantityError(ValueError):
    """An attempt to derive a number from a styled raster.

    A tile is a picture of a measurement. Sampling its pixels invents precision
    the tile never had, and the invented figure is indistinguishable downstream
    from a measured one — so an adapter that could only obtain tiles for a
    quantity has not obtained that quantity (D-133).
    """


@dataclass(frozen=True, slots=True)
class SourceDescriptor:
    """Everything that must be true of a source before it is fetched from.

    The same seven fields as ``meridian.store.ingest_sources.NewIngestSource``,
    deliberately not that type: this one refuses construction, and that one
    does not, because there the database is the check. Kept in step by
    ``tests/unit/test_ingest_contract.py``.

    Raises:
        TermsNotRecordedError: A field is blank, the source id is not ours to
            use as a directory name, the terms URL is not https, or a
            vocabulary value is unknown.
    """

    source_id: str
    """Ours, never the source's own name for itself, and pattern-checked
    because it is also a directory in the raw store (D-141)."""

    source_class: str
    name: str
    licence: str
    """As published. A label, not a judgement — what may be *done* with the
    data is decided by the terms, which is why both are recorded."""

    terms_url: str
    """https, for the reason ``ingest_sources`` CHECKs it: a terms page fetched
    over plain http is a terms page an intermediary can rewrite, and this is
    the evidence for a claim about what we were permitted to do."""

    attribution_entry: str
    """The ``ATTRIBUTION.md`` entry this source was recorded under.

    A record then traces to the terms it arrived under, rather than to whatever
    that file happens to say today.
    """

    access_constraint: str

    def __post_init__(self) -> None:
        """Refuse a source whose terms nobody has written down."""
        for field_name in ("name", "licence", "attribution_entry"):
            if not str(getattr(self, field_name)).strip():
                message = (
                    f"{field_name} is empty; a source cannot be fetched from until "
                    "its terms are recorded (D-134)"
                )
                raise TermsNotRecordedError(message)
        if not SOURCE_ID.fullmatch(self.source_id):
            message = (
                f"source_id {self.source_id!r} must match {SOURCE_ID.pattern}: it is "
                "also a directory name in the raw store"
            )
            raise TermsNotRecordedError(message)
        if not self.terms_url.startswith("https://"):
            message = (
                f"terms_url {self.terms_url!r} is not https, so it is not evidence "
                "of anything an intermediary could not have written"
            )
            raise TermsNotRecordedError(message)
        _one_of("source_class", self.source_class, SOURCE_CLASSES)
        _one_of("access_constraint", self.access_constraint, ACCESS_CONSTRAINTS)


@dataclass(frozen=True, slots=True)
class FetchRequest:
    """What an operator asked a source for.

    Raises:
        ValueError: The interval runs backwards, or the limit is not positive.

    Note:
        ``limit`` is the count of artefacts, and it is not optional. A fetch
        with no ceiling is one that discovers a source's size by exhausting it,
        against terms that often count requests — so the budget is part of the
        request rather than a policy somewhere above it (D-134's
        ``key_counted``).
    """

    since: datetime | None = None
    until: datetime | None = None
    limit: int = 50

    def __post_init__(self) -> None:
        """Refuse a request that cannot be honoured politely."""
        for name, moment in (("since", self.since), ("until", self.until)):
            if moment is not None and moment.tzinfo is None:
                message = f"{name} is naive; every instant here is UTC"
                raise ValueError(message)
        if (
            self.since is not None
            and self.until is not None
            and self.until < self.since
        ):
            message = f"until {self.until} precedes since {self.since}"
            raise ValueError(message)
        if self.limit < 1:
            message = f"limit is {self.limit}; a fetch of nothing is not a fetch"
            raise ValueError(message)


@runtime_checkable
class Adapter(Protocol):
    """One source, as the fetch path sees it."""

    @property
    def descriptor(self) -> SourceDescriptor:
        """What this source is and what it may be used under."""
        ...

    def plan(self, request: FetchRequest) -> tuple[RemoteArtefact, ...]:
        """Which artefacts to ask for, in the order to ask for them.

        Args:
            request: The interval and the ceiling an operator gave.

        Returns:
            At most ``request.limit`` artefacts. **Names, not bytes** — nothing
            here opens a socket, which is what lets every adapter be planned
            against in a test with nothing reachable.
        """
        ...

    def source_version(self, retrieved: RetrievedArtefact) -> str:
        """The source's own version of one artefact, read from its response.

        Args:
            retrieved: The response, usually consulted for an ``ETag`` or a
                ``Last-Modified``. Which header carries the version is the
                source's business, which is why this is asked of the adapter.

        Returns:
            A non-empty version string. An empty one refuses the retrieval
            before anything is published, via :func:`require_source_version`,
            because an artefact of unknown vintage cannot be compared against
            its successor.
        """
        ...


@runtime_checkable
class Normaliser(Protocol):
    """One artefact format, as the offline path sees it."""

    transformation_version: str
    """A module constant, bumped when the output changes.

    Part of the stored key, and pinned by a golden digest per version — so
    changing what a normaliser produces without bumping it breaks a test rather
    than silently restating every row already loaded (D-140).
    """

    def normalise(self, artefact: StoredArtefact) -> NormalisedBatch:
        """Turn one stored artefact into rows.

        Args:
            artefact: The bytes and the manifest, read from the raw store. The
                **only** argument, deliberately: no retriever, no adapter, no
                URL, no clock. There is nothing here through which a network
                could be reached, which is the whole of D-142's guarantee.

        Returns:
            The batch this artefact normalises to.

        Raises:
            TileIsNotAQuantityError: The artefact is a tile.
            NormalisationError: The bytes do not describe rows this schema can
                hold.
        """
        ...


def require_source_version(version: str, remote: RemoteArtefact) -> str:
    """Hold an adapter to returning a version, before anything is written.

    Args:
        version: Whatever :meth:`Adapter.source_version` returned.
        remote: The artefact it was read from, so the refusal names it.

    Returns:
        The version, stripped.

    Raises:
        ValueError: It is empty. ``ingest_records.source_version`` is NOT NULL
            and non-empty for the same reason; refusing here means the fetch
            stops with nothing published, rather than leaving bytes in the raw
            store that no row can ever point at.
    """
    stripped = version.strip()
    if not stripped:
        message = (
            f"{remote.original_identifier} returned no source version, so it could "
            "never be compared against its successor"
        )
        raise ValueError(message)
    return stripped


def refuse_a_tile(provenance: Provenance) -> None:
    """Stop a normaliser that was handed a picture of a measurement.

    Args:
        provenance: The artefact's declared provenance, carrying its payload
            kind.

    Raises:
        TileIsNotAQuantityError: The payload kind is ``tile``.

    Note:
        Every normaliser calls this first. A tile may be displayed and
        referenced; no query may derive a value from one (D-133), and the only
        way to keep that true is for the code path that would derive one to not
        exist.
    """
    if provenance.payload_kind == "tile":
        message = (
            f"{provenance.original_identifier} is a tile. A tile is a picture of a "
            "measurement: sampling its pixels invents precision it never had, and "
            "nothing downstream could tell the invented figure from a measured one "
            "(D-133)"
        )
        raise TileIsNotAQuantityError(message)


def _one_of(name: str, value: str, allowed: tuple[str, ...]) -> None:
    """Hold one field to its vocabulary."""
    if value not in allowed:
        message = f"{name} {value!r} is not one of {', '.join(allowed)}"
        raise TermsNotRecordedError(message)
