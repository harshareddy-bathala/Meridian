"""What to fetch, what came back, and the one seam a network lives behind.

An adapter says *which* artefacts it wants; a retriever gets them. Splitting
those two is what lets every adapter be exercised against recorded fixtures
with nothing reachable (D-142) — the adapter names artefacts, and what turns a
name into bytes is supplied from outside.

**This is the only module in the distribution whose types mention a network at
all.** Nothing downstream of the raw store takes a :class:`Retriever`, a URL or
a header, so normalisation could not reach a source even if someone wanted it
to. That is what makes the stage's completion gate provable rather than
asserted.

Reference: docs/DECISIONS.md D-133, D-141, D-142.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from meridian_ingest.provenance import PAYLOAD_KINDS, check_interval
from meridian_ingest.raw_layout import digest_on_disk

__all__ = [
    "FIXTURE_MEDIA_TYPES",
    "FixtureRetriever",
    "RemoteArtefact",
    "RetrievalError",
    "RetrievedArtefact",
    "Retriever",
]

FIXTURE_MEDIA_TYPES = {
    ".json": "application/json",
    ".csv": "text/csv",
    ".png": "image/png",
}
"""What a fixture file's suffix says it is.

Small and closed on purpose: a fixture whose type nobody declared is one whose
``media_type`` would be guessed, and that value is stored as though a source
had said it.
"""

_READ_BLOCK = 1 << 16


class RetrievalError(Exception):
    """A fetch that could not be completed, or should not be attempted."""


@dataclass(frozen=True, slots=True)
class RemoteArtefact:
    """One artefact an adapter has decided to ask for.

    Raises:
        RetrievalError: The URL is not https, the identifier is blank, or the
            payload kind is not one of
            :data:`~meridian_ingest.provenance.PAYLOAD_KINDS`.
    """

    url: str
    """**https only.** A resource fetched over plain http is one an
    intermediary can rewrite, and the digest we take would then faithfully
    record what the intermediary said. The same argument
    ``ingest_sources.terms_url`` is CHECKed by."""

    original_identifier: str
    """The source's own identifier for this artefact, as the adapter reads it.

    Carried to the manifest and to ``ingest_records`` and to nowhere else;
    in particular never to a path (D-141), which is why nothing here
    constrains its shape beyond being present.
    """

    payload_kind: str
    """``data`` or ``tile``. Decided at fetch time, because a normaliser must be
    able to refuse a tile (D-133) and cannot tell from the bytes."""

    headers: Mapping[str, str] = field(default_factory=dict)
    """Request headers this artefact needs, such as a conditional ``If-None-
    Match``. Never credentials: a source that needs a key is
    ``access_constraint = 'key_counted'`` and the key comes from configuration,
    not from an adapter's literal."""

    valid_from: datetime | None = None
    valid_to: datetime | None = None
    """The interval this artefact is expected to *describe*.

    Planning information, and the reason the adapter asked for it: a listing
    that says "August" is what made August's file worth fetching. It travels
    with the name rather than being read from the bytes because it reaches
    ``ingest_records.valid_from``, where it is what a feature lookup selects on
    (D-131) — and a value read out of a normaliser would arrive too late, after
    the row already exists.
    """

    spatial_extent: dict[str, float] | None = None
    """The ground it covers, as a bounding box, or None where it covers none."""

    def __post_init__(self) -> None:
        """Refuse an artefact that should not be asked for."""
        if not self.url.startswith("https://"):
            message = (
                f"{self.url!r} is not https; an artefact fetched over plain http "
                "is one an intermediary can rewrite, and our digest would record "
                "the rewrite as evidence"
            )
            raise RetrievalError(message)
        if not self.original_identifier.strip():
            message = f"{self.url} carries no identifier to cite it by"
            raise RetrievalError(message)
        if self.payload_kind not in PAYLOAD_KINDS:
            kinds = ", ".join(PAYLOAD_KINDS)
            message = f"payload_kind {self.payload_kind!r} is not one of {kinds}"
            raise RetrievalError(message)
        check_interval(self.valid_from, self.valid_to)


@dataclass(frozen=True, slots=True)
class RetrievedArtefact:
    """What came back, before anything has been written down.

    Note:
        ``chunks`` is an iterable and is consumed exactly once, by
        :meth:`~meridian_ingest.raw_store.RawStore.publish`. Holding a whole
        artefact in memory to hash it would make the size of what we can ingest
        a property of the machine doing the ingesting.
    """

    remote: RemoteArtefact
    chunks: Iterable[bytes]
    media_type: str
    """What the response said the bytes are. Stored; never used to pick a
    parser, because a normaliser is chosen by the adapter and not by a header a
    source controls."""

    headers: Mapping[str, str] = field(default_factory=dict)
    """The response headers, carried because ``source_version`` is usually one
    of them — an ``ETag`` or a ``Last-Modified``. Which one is the adapter's
    business, which is why this is passed to it rather than read here."""


@runtime_checkable
class Retriever(Protocol):
    """Turns one :class:`RemoteArtefact` into bytes.

    The seam a network lives behind. A real implementation opens a socket; the
    fixture-backed one reads a file; the gate test supplies one that fails if
    called. Nothing above this line knows which it has.
    """

    def retrieve(self, remote: RemoteArtefact) -> RetrievedArtefact:
        """Fetch one artefact.

        Args:
            remote: What to ask for.

        Returns:
            The response, with its bytes still unread.

        Raises:
            RetrievalError: The artefact could not be fetched.
        """
        ...


class FixtureRetriever:
    """Serves artefacts from a flat directory of files instead of a network.

    Args:
        root: The directory holding the fixture files.

    Note:
        **The adapter above it cannot tell.** It plans the same https URLs it
        would plan against the real source, and only the last path segment is
        used to find a file — so what is exercised is the adapter that would be
        deployed, not a test-shaped variant of it (D-142).

        The URLs the reference adapter plans are under ``.invalid``, which is
        reserved and never resolves, so even wiring a real retriever underneath
        it cannot reach anything.
    """

    def __init__(self, root: Path) -> None:
        """Point at ``root``. Nothing is read yet."""
        self._root = root

    def retrieve(self, remote: RemoteArtefact) -> RetrievedArtefact:
        """Read one fixture as though it had been fetched.

        Args:
            remote: What the adapter asked for. Its URL's final segment names
                the file.

        Returns:
            The bytes as a stream, with a media type from the suffix and an
            ``ETag`` over the content.

        Raises:
            RetrievalError: The URL does not name a plain file, the file is
                absent, or its suffix is not in :data:`FIXTURE_MEDIA_TYPES`.

        Note:
            The ``ETag`` is the file's own digest, which is what a real server
            computes and means an unchanged fixture re-fetches to the same
            ``source_version``. It costs a second read of the file; a fixture
            is small, and inventing a version instead would make the one value
            that detects a changed artefact depend on when the test ran.
        """
        path = self._path_for(remote.url)
        suffix = path.suffix.lower()
        if suffix not in FIXTURE_MEDIA_TYPES:
            known = ", ".join(sorted(FIXTURE_MEDIA_TYPES))
            message = f"{path.name} has no declared media type; known suffixes: {known}"
            raise RetrievalError(message)
        if not path.is_file():
            message = f"{remote.url} has no fixture at {path}"
            raise RetrievalError(message)

        digest, _ = digest_on_disk(path)
        media_type = FIXTURE_MEDIA_TYPES[suffix]
        return RetrievedArtefact(
            remote=remote,
            chunks=_blocks(path),
            media_type=media_type,
            headers={"ETag": f'"{digest.hex()[:16]}"', "Content-Type": media_type},
        )

    def _path_for(self, url: str) -> Path:
        """The fixture file one URL names, refusing anything that is not one.

        The same rule the raw store applies to a stored path: a name, never a
        route through the filesystem. A fixture directory is flat, so a
        separator or a dot-segment in the final element is not a fixture that
        is missing — it is a URL trying to be a path.
        """
        name = url.rstrip("/").rsplit("/", 1)[-1]
        if not name or name.startswith(".") or "\\" in name:
            message = f"{url!r} does not name a fixture file"
            raise RetrievalError(message)
        return self._root / name


def _blocks(path: Path) -> Iterator[bytes]:
    """The file's bytes, a block at a time, as a response body would arrive."""
    with path.open("rb") as handle:
        while block := handle.read(_READ_BLOCK):
            yield block
