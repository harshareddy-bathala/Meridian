"""The ``meridian-ingest`` command — the operator's side of archive ingest.

Five verbs, and the division between them is the stage's whole argument.
``fetch`` is the only one that opens a socket; ``normalise``, ``load`` and
``verify`` read the raw store and could not reach an archive if they wanted to.
So "downloaded once, then normalised and evaluated with no network" is
something an operator does at a shell prompt rather than something a document
claims (D-142).

**A separate binary, not a ``meridian`` subcommand.** ``meridian.cli`` imports
every ``cli_*`` module eagerly, so a subcommand would make the platform import
the archive layer at start-up — an external archive one import from the
scheduling path (D-138). It also means an operator who never installs this
still has a complete Meridian, which is the independence test visible at a
shell prompt.

**Exit codes**, following ``meridian.cli``: 0 success, 1 ran and failed, 2 a
usage error, and **3 for a checksum mismatch**. Three is its own code because a
monitoring script has to tell "verify could not run" from "the archive on disk
is not what we downloaded", and those call for different people.

Reference: docs/DECISIONS.md D-133, D-134, D-138, D-141, D-142.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from meridian.config import load_settings as load_platform_settings
from meridian.store.archive_observations import NormalisationDisagreementError
from meridian.store.pool import DatabaseUnreachableError, connect_once
from meridian_ingest import __version__
from meridian_ingest.adapters import REGISTRY, UnknownSourceError, normaliser_for
from meridian_ingest.cli_fetch import run_fetch
from meridian_ingest.config import ConfigurationError, IngestSettings, load_settings
from meridian_ingest.console import refuse as _refuse
from meridian_ingest.console import say as _say
from meridian_ingest.console import warn as _warn
from meridian_ingest.load import TermsChangedError, load_source
from meridian_ingest.normalise.records import NormalisationError
from meridian_ingest.raw_store import RawStore, RawStoreError

__all__ = ["EXIT_CORRUPT", "EXIT_FAILED", "EXIT_USAGE", "main"]

EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_CORRUPT = 3
"""A stored artefact no longer matches the digest taken when it arrived.

Distinct from :data:`EXIT_FAILED` because the two need different responses: one
is "the command could not run", the other is "the one thing that cannot be
recreated without going back to the source is damaged".
"""


def main(argv: Sequence[str] | None = None) -> int:
    """Run one command.

    Args:
        argv: Arguments after the program name. Reads ``sys.argv`` when omitted.

    Returns:
        A process exit code.
    """
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    try:
        settings = load_settings(args.config)
    except ConfigurationError as exc:
        return _refuse(f"meridian-ingest: {exc}")
    try:
        return _dispatch(args, settings)
    except (
        UnknownSourceError,
        RawStoreError,
        ConfigurationError,
        TermsChangedError,
        NormalisationError,
        NormalisationDisagreementError,
    ) as exc:
        return _refuse(f"meridian-ingest {args.command}: {exc}")


def _dispatch(args: argparse.Namespace, settings: IngestSettings) -> int:
    """Send one parsed invocation to the verb that handles it.

    A table rather than a chain of comparisons: every verb has the same shape,
    so there is no decision here to read — which is the point, because the
    decisions worth reading are inside the verbs.
    """
    if args.source is not None and args.source not in REGISTRY:
        known = ", ".join(sorted(REGISTRY))
        message = f"no source is registered as {args.source!r}; registered: {known}"
        raise UnknownSourceError(message)
    verbs = {
        "sources": _run_sources,
        "fetch": _run_fetch,
        "normalise": _run_normalise,
        "verify": _run_verify,
        "load": _run_load,
    }
    return verbs[args.command](args, settings)


def _run_fetch(args: argparse.Namespace, settings: IngestSettings) -> int:
    """``fetch``, with the sources resolved before anything is opened."""
    return run_fetch(args, settings, _chosen(args, settings))


def _run_sources(args: argparse.Namespace, settings: IngestSettings) -> int:
    """List every source this installation can fetch, and under what terms.

    The terms are printed rather than the counts, because the question this
    command answers is "may we take this, and did we say so" (D-134).

    Lists disabled sources too, unlike every other verb: a source switched off
    in the settings file still has terms, and hiding it would answer the
    question wrongly.
    """
    for source_id in sorted(REGISTRY):
        if args.source is not None and source_id != args.source:
            continue
        descriptor = REGISTRY[source_id].adapter.descriptor
        source = settings.for_source(source_id)
        state = "enabled" if source.enabled else "disabled"
        _say(f"{source_id}  [{state}, budget {source.request_budget}]")
        _say(f"    {descriptor.name} — {descriptor.source_class}")
        _say(f"    licence: {descriptor.licence}")
        _say(f"    terms:   {descriptor.terms_url}")
        _say(f"    credited as: {descriptor.attribution_entry}")
    return 0


def _run_normalise(args: argparse.Namespace, settings: IngestSettings) -> int:
    """Normalise every stored artefact without writing anything.

    The offline half of the completion gate, and the command to run twice: it
    prints each reception's digest, so two runs of a snapshot are compared with
    ``diff`` rather than trusted.
    """
    store = RawStore(settings.raw_root)
    for source_id in _chosen(args, settings):
        normaliser = normaliser_for(source_id)
        for raw_path in store.scan(source_id):
            artefact = store.read(raw_path)
            if artefact.manifest.provenance.payload_kind == "tile":
                _say(f"{raw_path}  tile, nothing derived (D-133)")
                continue
            batch = normaliser.normalise(artefact)
            _say(
                f"{raw_path}  {len(batch.stations)} stations, "
                f"{len(batch.receptions)} receptions, "
                f"version {batch.transformation_version}"
            )
            for reception in batch.receptions:
                _say(
                    f"    {reception.source_observation_id}  "
                    f"{reception.content_sha256.hex()[:16]}"
                )
    return 0


def _run_verify(args: argparse.Namespace, settings: IngestSettings) -> int:
    """Re-hash every stored artefact against the digest taken when it arrived."""
    store = RawStore(settings.raw_root)
    damaged: list[str] = []
    checked = 0
    for source_id in _chosen(args, settings):
        for raw_path in store.scan(source_id):
            checked += 1
            try:
                store.verify(raw_path)
            except RawStoreError as exc:
                damaged.append(str(exc))
    if damaged:
        for one in damaged:
            _warn(f"meridian-ingest verify: {one}")
        _warn(
            f"{len(damaged)} of {checked} artefacts do not match their manifests. "
            "The raw store is not in the database backup — restore it from your "
            "own copy (docs/OPERATIONS.md)."
        )
        return EXIT_CORRUPT
    _say(f"{checked} artefacts match the digests taken when they arrived.")
    return 0


def _run_load(args: argparse.Namespace, settings: IngestSettings) -> int:
    """Load every stored artefact into the archive tables."""
    store = RawStore(settings.raw_root)
    try:
        conn = connect_once(load_platform_settings())
    except DatabaseUnreachableError as exc:
        return _refuse(f"meridian-ingest load: {exc}")
    # Autocommit, so each artefact's `conn.transaction()` in `load_artefact` is
    # a real transaction that commits when it closes. Without it the first
    # statement opens one run-wide transaction, every artefact becomes a
    # savepoint inside it, and a failure in the last artefact rolls back all
    # the ones that had finished — the opposite of "resumed by running again".
    conn.autocommit = True
    with conn:
        for source_id in _chosen(args, settings):
            report = load_source(conn, store, source_id)
            _say(
                f"{source_id}: {report.records_written} artefacts, "
                f"{report.stations_written} stations, "
                f"{report.receptions_written} receptions written"
            )
            if report.receptions_already_held:
                _say(
                    f"    {report.receptions_already_held} receptions were already "
                    "held, identically"
                )
            for skipped in report.skipped:
                _say(f"    {skipped.raw_path}: {skipped.skipped}, nothing derived")
            if report.wrote_nothing():
                _say("    nothing new — this tree was already loaded")
    return 0


def _chosen(args: argparse.Namespace, settings: IngestSettings) -> tuple[str, ...]:
    """Which sources this invocation acts on.

    Note:
        Already known to be registered — :func:`_dispatch` checks that once, so
        every verb can trust it.

        A named source is used whether or not the settings file disabled it:
        naming it is the operator overriding their own default, and silently
        doing nothing would look like an empty archive.
    """
    if args.source is not None:
        return (args.source,)
    return settings.enabled_sources()


def _parser() -> argparse.ArgumentParser:
    """The command tree."""
    parser = argparse.ArgumentParser(
        prog="meridian-ingest",
        description=(
            "Retrieve, normalise and load external archive data. Only `fetch` "
            "touches a network."
        ),
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="settings file; default $MERIDIAN_INGEST_CONFIG, then ./ingest.toml",
    )
    commands = parser.add_subparsers(dest="command")

    _add_source(
        commands.add_parser("sources", help="what may be fetched, and under what terms")
    )

    fetch = commands.add_parser("fetch", help="retrieve artefacts (opens a socket)")
    _add_source(fetch)
    fetch.add_argument("--since", type=_instant, default=None, help="UTC ISO 8601")
    fetch.add_argument("--until", type=_instant, default=None, help="UTC ISO 8601")
    fetch.add_argument(
        "--limit", type=int, default=None, help="most artefacts to retrieve"
    )

    _add_source(commands.add_parser("normalise", help="normalise, writing nothing"))
    _add_source(commands.add_parser("load", help="load into the archive tables"))
    _add_source(commands.add_parser("verify", help="re-hash the raw store; exits 3"))
    return parser


def _add_source(parser: argparse.ArgumentParser) -> None:
    """``--source``, which every verb takes and none of them requires."""
    parser.add_argument(
        "--source", default=None, help="one source id; default every enabled source"
    )


def _instant(value: str) -> datetime:
    """One command-line timestamp, which must say what zone it is in.

    An operator running this may not be in UTC, and a bound shifted by their
    local offset selects a different month of artefacts — quietly, and with
    every retrieved file looking exactly as legitimate as the right one. The
    same refusal ``meridian passes generate`` makes.
    """
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        message = f"{value!r} is not an ISO 8601 instant"
        raise argparse.ArgumentTypeError(message) from exc
    if parsed.tzinfo is None:
        message = f"{value!r} carries no offset; write it as 2026-08-01T00:00:00Z"
        raise argparse.ArgumentTypeError(message)
    return parsed


if __name__ == "__main__":  # pragma: no cover — the console script calls main
    raise SystemExit(main())
