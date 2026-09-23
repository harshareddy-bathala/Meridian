"""One adapter per source, each answering the same two questions.

An adapter knows a source: what it publishes, under what terms, and which
artefacts to ask for. A normaliser knows an artefact's format. They are
separate because the first touches a network and the second must never be able
to (D-142), and separating them by *type* rather than by convention is what
makes that checkable.

The contract is :mod:`meridian_ingest.adapters.protocol`. This module is the
registry: a literal table of the sources that exist, so ``meridian-ingest
sources list`` and ``fetch`` reach one by id without importing it by name.

**Adding a source is adding a row here**, and the row cannot be added before
the adapter carries its licence, its terms and its ``ATTRIBUTION.md`` entry —
:class:`~meridian_ingest.adapters.protocol.SourceDescriptor` refuses to exist
otherwise. D-134 asks for the attribution to land before the first retrieval;
this is where "before" stops depending on anyone remembering.

The first and only source is fixture-backed and ours. A recorded fixture from a
real archive would be that archive's data committed to a public repository,
which is the redistribution question D-136 leaves open, and settling it by
accident in a commit about test plumbing is not a decision.

Reference: docs/DECISIONS.md D-134, D-136, D-142.
"""

from __future__ import annotations

from dataclasses import dataclass

from meridian_ingest.adapters.protocol import Adapter, Normaliser, SourceDescriptor
from meridian_ingest.adapters.reference import (
    FIXTURE_ROOT,
    ReferenceAdapter,
    ReferenceNormaliser,
)
from meridian_ingest.retrieval import FixtureRetriever, Retriever

__all__ = [
    "REGISTRY",
    "Registration",
    "UnknownSourceError",
    "adapter_for",
    "normaliser_for",
    "registered_sources",
]


class UnknownSourceError(LookupError):
    """A source id nothing is registered under.

    A ``LookupError`` rather than the ``KeyError`` beneath it, because this is
    printed to an operator and ``KeyError.__str__`` renders its argument with
    ``repr`` — a sentence about a missing source would arrive wrapped in
    quotation marks. Still a lookup failure, still caught by
    ``except LookupError``.
    """


@dataclass(frozen=True, slots=True)
class Registration:
    """One source's pair: how to fetch it, and how to read what came back."""

    adapter: Adapter
    normaliser: Normaliser
    retriever: Retriever | None = None
    """How its artefacts are obtained, when it is not plain HTTP.

    The reference archive serves the synthetic files shipped beside it, so it
    declares that here rather than leaving ``fetch`` to recognise an id and
    special-case it. A source that says nothing is fetched over HTTP, under the
    budget and backoff in :mod:`meridian_ingest.politeness`.
    """


def _registry() -> dict[str, Registration]:
    """The table, checked as it is built.

    Each entry is keyed by the adapter's *own* declared source id rather than
    by a string written beside it, so the key and the descriptor cannot
    disagree — and a disagreement there would mean artefacts published into one
    source's directory in the raw store and recorded against another's row.
    """
    pairs = [
        Registration(
            ReferenceAdapter(),
            ReferenceNormaliser(),
            FixtureRetriever(FIXTURE_ROOT),
        )
    ]
    return {pair.adapter.descriptor.source_id: pair for pair in pairs}


REGISTRY = _registry()


def registered_sources() -> tuple[SourceDescriptor, ...]:
    """Every source this installation can fetch from, in id order.

    Returns:
        Their descriptors, each carrying the licence and terms it was
        registered under.
    """
    return tuple(
        REGISTRY[source_id].adapter.descriptor for source_id in sorted(REGISTRY)
    )


def adapter_for(source_id: str) -> Adapter:
    """The adapter registered under one source id.

    Args:
        source_id: As ``ingest_sources.source_id`` holds it.

    Returns:
        The adapter.

    Raises:
        UnknownSourceError: Nothing is registered under that id.
    """
    return _registration(source_id).adapter


def normaliser_for(source_id: str) -> Normaliser:
    """The normaliser registered under one source id.

    Args:
        source_id: As ``ingest_sources.source_id`` holds it.

    Returns:
        The normaliser.

    Raises:
        UnknownSourceError: Nothing is registered under that id.

    Note:
        Looked up by source rather than by media type or by sniffing the bytes.
        A normaliser chosen from what an artefact looks like is a normaliser a
        source could choose for us by changing what it serves.
    """
    return _registration(source_id).normaliser


def _registration(source_id: str) -> Registration:
    """One source's pair, or a refusal naming the ids that do exist."""
    try:
        return REGISTRY[source_id]
    except KeyError as exc:
        known = ", ".join(sorted(REGISTRY)) or "none"
        message = f"no source is registered as {source_id!r}; registered: {known}"
        raise UnknownSourceError(message) from exc
