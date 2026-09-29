"""``meridian-ingest fetch`` — the only command in the project that opens a socket.

Split from :mod:`meridian_ingest.cli` because it is the one verb with something
to decide, and because the decision is made *before* the socket: D-134 asks for
a source's attribution entry to land before its first retrieval, and this is
where "before" stops depending on anybody remembering.

**What the check can and cannot do.** ``ATTRIBUTION.md`` is a file in the
repository, not something shipped in a wheel, so an installation outside a
checkout has nothing to check against. Found and missing the entry is a
refusal; not found at all is said out loud and the fetch continues — the
obligation is enforced where it actually lives, by a test over every registered
source, and a runtime check that silently passed would be worse than one that
says it could not run.

Reference: docs/DECISIONS.md D-133, D-134, D-141, D-142.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from meridian_ingest.adapters import REGISTRY
from meridian_ingest.adapters.listing import LISTING_SUFFIX, ListingAdapter
from meridian_ingest.adapters.protocol import (
    FetchRequest,
    SourceDescriptor,
    require_source_version,
)
from meridian_ingest.config import IngestSettings, SourceSettings
from meridian_ingest.console import say, warn
from meridian_ingest.credentials import MissingKeyError, read_api_key
from meridian_ingest.http_retriever import (
    USER_AGENT,
    HttpRetriever,
    SourceAccess,
    open_client,
)
from meridian_ingest.politeness import BudgetExhaustedError, RequestBudget
from meridian_ingest.provenance import Provenance
from meridian_ingest.rate_ledger import RateLimiter, RequestLedger
from meridian_ingest.raw_store import PublishedArtefact, RawStore
from meridian_ingest.retrieval import (
    RemoteArtefact,
    RetrievalError,
    RetrievedArtefact,
    Retriever,
)

__all__ = [
    "ATTRIBUTION_NAME",
    "AttributionCheck",
    "Window",
    "check_attribution",
    "fetch_source",
    "permitted",
    "run_fetch",
]

ATTRIBUTION_NAME = "ATTRIBUTION.md"


@dataclass(frozen=True, slots=True)
class Window:
    """The interval and ceiling one fetch asks for, from a command or a timer."""

    since: datetime | None = None
    until: datetime | None = None
    limit: int | None = None


@dataclass(frozen=True, slots=True)
class AttributionCheck:
    """Whether a source's credit is written down where it claims to be."""

    file: Path | None
    """The ``ATTRIBUTION.md`` that was read, or None when none was found."""

    entry_found: bool

    @property
    def refuses(self) -> bool:
        """Whether this stops the fetch.

        Only when the file was found and the entry was not. A file we could not
        find proves nothing either way, and refusing on it would make the
        command unusable outside a checkout for a reason that has nothing to do
        with the terms.
        """
        return self.file is not None and not self.entry_found


def check_attribution(
    descriptor: SourceDescriptor, start: Path | None = None
) -> AttributionCheck:
    """Look for a source's attribution entry, walking up from ``start``.

    Args:
        descriptor: The source, carrying the entry it claims to be credited
            under.
        start: Where to begin looking. Defaults to the working directory.

    Returns:
        What was found.
    """
    here = Path.cwd() if start is None else start
    for directory in (here, *here.parents):
        candidate = directory / ATTRIBUTION_NAME
        if candidate.is_file():
            text = candidate.read_text(encoding="utf-8")
            return AttributionCheck(
                file=candidate, entry_found=descriptor.attribution_entry in text
            )
    return AttributionCheck(file=None, entry_found=False)


def run_fetch(
    args: argparse.Namespace, settings: IngestSettings, sources: tuple[str, ...]
) -> int:
    """Retrieve artefacts for each chosen source into the raw store.

    Args:
        args: The parsed invocation — ``since``, ``until``, ``limit``.
        settings: The settings file, or the defaults.
        sources: Which sources to fetch, already resolved by the caller.

    Returns:
        0, or 1 when a source was refused or could not be fetched.

    Note:
        A budget or a ``Retry-After`` that stops one source does not stop the
        rest: each is a separate agreement with a separate archive, and one
        that asked us to come back later has said nothing about the others.
    """
    store = RawStore(settings.raw_root)
    failed = False
    for source_id in sources:
        if not permitted(source_id):
            failed = True
            continue
        window = Window(args.since, args.until, args.limit)
        failed = not fetch_source(source_id, window, settings, store) or failed
    return 1 if failed else 0


def permitted(source_id: str) -> bool:
    """Whether this source is credited where it says it is — before any socket."""
    descriptor = REGISTRY[source_id].adapter.descriptor
    check = check_attribution(descriptor)
    if check.refuses:
        warn(
            f"meridian-ingest fetch: refusing to fetch {source_id} — {check.file} "
            f"does not mention {descriptor.attribution_entry!r}. A source is "
            "credited before its first retrieval, not after (D-134)."
        )
        return False
    if check.file is None:
        warn(
            f"meridian-ingest fetch: no {ATTRIBUTION_NAME} above {Path.cwd()}, so "
            f"{source_id}'s credit could not be checked here."
        )
    return True


def fetch_source(
    source_id: str, window: Window, settings: IngestSettings, store: RawStore
) -> bool:
    """Fetch one source's planned artefacts, and any a listing names.

    Args:
        source_id: A registered source, already found credited.
        window: The interval and the ceiling.
        settings: For the source's places, budget and key variable.
        store: Where the bytes go.

    Returns:
        Whether it finished.
    """
    source = settings.for_source(source_id)
    registration = REGISTRY[source_id]
    request = FetchRequest(
        since=window.since,
        until=window.until,
        limit=window.limit if window.limit is not None else source.request_budget,
        points=source.places.points,
        bbox=source.places.bbox,
        layers=source.places.layers,
    )
    try:
        planned = registration.adapter.plan(request)
    except ValueError as exc:
        warn(f"meridian-ingest fetch: {exc}")
        return False
    say(f"{source_id}: {len(planned)} artefacts to retrieve")
    with ExitStack() as stack:
        try:
            retriever = _retriever(stack, source_id, settings, source)
            for remote in planned:
                published = _retrieve(retriever, source_id, remote, store)
                say(f"    {published.raw_path}  {_held(published.written)}")
                for more in _expanded(source_id, store, published.raw_path, request):
                    again = _retrieve(retriever, source_id, more, store)
                    say(f"      {again.raw_path}  {_held(again.written)}")
        except BudgetExhaustedError as exc:
            warn(f"meridian-ingest fetch: {exc}")
            return False
        except (RetrievalError, MissingKeyError, ValueError) as exc:
            warn(f"meridian-ingest fetch: {source_id}: {exc}")
            return False
    return True


def _expanded(
    source_id: str, store: RawStore, raw_path: str, request: FetchRequest
) -> tuple[RemoteArtefact, ...]:
    """The granules a just-stored listing names, or nothing for anything else."""
    adapter = REGISTRY[source_id].adapter
    stored = store.read(raw_path)
    if not isinstance(adapter, ListingAdapter) or not (
        stored.manifest.provenance.original_identifier.endswith(LISTING_SUFFIX)
    ):
        return ()
    return adapter.expand(stored, request)


def _retrieve(
    retriever: Retriever, source_id: str, remote: RemoteArtefact, store: RawStore
) -> PublishedArtefact:
    """One artefact: fetch it, name its version, and publish it unchanged."""
    retrieved = retriever.retrieve(remote)
    version = require_source_version(
        REGISTRY[source_id].adapter.source_version(retrieved), remote
    )
    return store.publish(
        _provenance(source_id, remote, retrieved, version), retrieved.chunks
    )


def _held(written: bool) -> str:
    return "retrieved" if written else "already held"


def _retriever(
    stack: ExitStack,
    source_id: str,
    settings: IngestSettings,
    source: SourceSettings,
) -> Retriever:
    """The thing that turns a planned artefact into bytes.

    A source may supply its own — the reference archive serves the synthetic
    files shipped beside it — and everything else is fetched over HTTP, with
    the source's key substituted at the socket and its published limits
    counted in the ledger beside the raw store (D-223).
    """
    registration = REGISTRY[source_id]
    if registration.retriever is not None:
        return registration.retriever
    # Read before any request: a source that answers 401 to everything would
    # otherwise spend its whole budget finding that out.
    key = read_api_key(source_id, source.key_env)
    limiter = RateLimiter(
        registration.rate_limits,
        RequestLedger.for_source(settings.raw_root, registration.ledger_id),
        max_wait_s=settings.retry.max_delay_s,
    )
    client = open_client(_user_agent(settings), settings.timeout_s)
    return stack.enter_context(
        HttpRetriever(
            client,
            RequestBudget(source.request_budget),
            settings.retry,
            SourceAccess(key=key, limiter=limiter),
        )
    )


def _user_agent(settings: IngestSettings) -> str:
    """Who we are, plus a contact when the settings file gave one."""
    if settings.contact is None:
        return USER_AGENT
    return f"{USER_AGENT} <{settings.contact}>"


def _provenance(
    source_id: str,
    remote: RemoteArtefact,
    retrieved: RetrievedArtefact,
    version: str,
) -> Provenance:
    """What the manifest beside the bytes will say.

    ``retrieved_at`` is taken here, once, at the moment of the retrieval —
    never at load time, which may be days later (D-141).
    """
    return Provenance(
        source_id=source_id,
        original_identifier=remote.original_identifier,
        source_version=version,
        payload_kind=remote.payload_kind,
        retrieved_at=datetime.now(tz=UTC),
        media_type=retrieved.media_type,
        valid_from=remote.valid_from,
        valid_to=remote.valid_to,
        spatial_extent=remote.spatial_extent,
    )
