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

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from meridian_ingest.provenance import PAYLOAD_KINDS

__all__ = [
    "RemoteArtefact",
    "RetrievalError",
    "RetrievedArtefact",
    "Retriever",
]


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
