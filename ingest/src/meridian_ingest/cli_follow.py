"""``meridian-ingest follow`` — near-real-time ingest, on a timer, off the platform.

Each round asks every enabled source that is due — its last retrieval older
than its cadence — for the interval since its lookback, and loads what arrived.
Run once from cron with ``--once``, or left running.

**It runs where ``meridian-ingest`` is installed, never in the jobs service.**
The platform image does not carry this distribution (D-138), and the jobs
service schedules passes: a source that hangs must not be able to delay a
schedule, and a source that is down must not be able to change one (D-131,
D-225). What ``follow`` writes, a later snapshot carries; nothing scheduled
reads it live.

**A source that fails does not stop the round.** Each is a separate agreement
with a separate service; one that is down or asked us to wait has said nothing
about the others, and the next round tries again.

Reference: docs/DECISIONS.md D-131, D-138, D-223, D-225.
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from meridian_ingest.adapters import REGISTRY
from meridian_ingest.cli_fetch import Window, fetch_source, permitted
from meridian_ingest.cli_load import run_load
from meridian_ingest.config import IngestSettings
from meridian_ingest.console import say
from meridian_ingest.rate_ledger import RateLimiter, RequestLedger
from meridian_ingest.raw_store import RawStore

__all__ = ["add_follow", "describe_limits", "due_sources", "run_follow"]

DEFAULT_INTERVAL_S = 300


def add_follow(
    commands: argparse._SubParsersAction[argparse.ArgumentParser],
    add_source: Callable[[argparse.ArgumentParser], None],
) -> None:
    """Add ``follow`` to the command tree."""
    follow = commands.add_parser(
        "follow", help="fetch each due source and load it, on a timer (opens sockets)"
    )
    add_source(follow)
    follow.add_argument("--once", action="store_true", help="one round, then exit")
    follow.add_argument(
        "--no-load", action="store_true", help="fetch only; load with `load` later"
    )
    follow.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_INTERVAL_S,
        help="seconds between rounds when not --once",
    )


def run_follow(args: argparse.Namespace, settings: IngestSettings) -> int:
    """Fetch and load each due source, once or until interrupted.

    Returns:
        0 after ``--once`` or an interrupt; 1 when a ``--once`` round had a
        source that could not be fetched.
    """
    sources = (args.source,) if args.source else settings.enabled_sources()
    while True:
        failed = follow_round(settings, sources, load=not args.no_load)
        if args.once:
            return 1 if failed else 0
        try:
            time.sleep(max(1, args.interval))
        except KeyboardInterrupt:  # pragma: no cover — an operator's Ctrl-C
            return 0


def follow_round(
    settings: IngestSettings,
    sources: tuple[str, ...],
    load: bool = True,
    now: datetime | None = None,
) -> bool:
    """One round: every due source fetched, then loaded. Returns whether any failed."""
    at = now or datetime.now(tz=UTC)
    store = RawStore(settings.raw_root)
    due = due_sources(store, sources, at)
    say(f"follow: {len(due)} of {len(sources)} sources due at {at.isoformat()}")
    fetched, failed = [], False
    for source_id in due:
        lookback = timedelta(seconds=REGISTRY[source_id].lookback_s)
        window = Window(since=at - lookback, until=at)
        if permitted(source_id) and fetch_source(source_id, window, settings, store):
            fetched.append(source_id)
        else:
            failed = True
    if load and fetched:
        failed = run_load(settings, tuple(fetched)) != 0 or failed
    return failed


def due_sources(
    store: RawStore, sources: tuple[str, ...], now: datetime
) -> tuple[str, ...]:
    """The sources whose newest retrieval is older than their cadence."""
    due = []
    for source_id in sources:
        held = store.scan(source_id)
        cadence = timedelta(seconds=REGISTRY[source_id].cadence_s)
        if not held:
            due.append(source_id)
            continue
        newest = store.read(held[-1]).manifest.provenance.retrieved_at
        if now - newest >= cadence:
            due.append(source_id)
    return tuple(due)


def describe_limits(settings: IngestSettings, source_id: str) -> list[str]:
    """What a source allows, what is left of it, and how often it is followed."""
    registration = REGISTRY[source_id]
    source = settings.for_source(source_id)
    lines = [f"per fetch: {source.request_budget} requests"]
    if registration.rate_limits:
        limiter = RateLimiter(
            registration.rate_limits,
            RequestLedger.for_source(settings.raw_root, registration.ledger_id),
        )
        left = ", ".join(
            f"{window.describe()} ({remaining} of {window.allowed} left)"
            for window, remaining in limiter.remaining().items()
        )
        lines.append(f"published limits: {left}")
    if source.key_env is not None:
        lines.append(f"key from: ${source.key_env}")
    lines.append(f"followed every {registration.cadence_s}s")
    return lines
