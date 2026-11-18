"""``meridian-ingest imagery`` — open imagery of the published areas.

``imagery search`` finds the clearest recent scene of each published area in
each chosen source, keeps what it read in the raw store, and prints each
choice with its reasons. It opens a socket, as ``fetch`` does, and it is held
to the same rules before it does: a source is refused unless its attribution
entry is written down (D-134), a run has a request budget, and every request
says who we are.

**The imagery libraries are imported when a verb runs, not before.** This
module is imported by ``meridian_ingest.cli`` on every invocation, and an
installation without the ``[imagery]`` extra must still run ``fetch`` and
``load``. What it imports at the top is the package's stdlib half — the areas,
the records, the sources — and the reader and the search arrive only after a
missing extra has been refused by name (D-371).

Reference: docs/DECISIONS.md D-134, D-220, D-326, D-371, D-372.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from meridian_ingest.cli_fetch import check_attribution
from meridian_ingest.config import IngestSettings
from meridian_ingest.console import refuse, say, warn
from meridian_ingest.http_retriever import USER_AGENT, HttpRetriever, open_client
from meridian_ingest.imagery import ImageryUnavailableError, require_imagery
from meridian_ingest.imagery.areas import PublishedArea, read_published_areas
from meridian_ingest.imagery.model import MalformedImageryError
from meridian_ingest.imagery.sources import (
    ImagerySource,
    UnknownImagerySourceError,
    imagery_source,
)
from meridian_ingest.politeness import RequestBudget
from meridian_ingest.raw_store import RawStore
from meridian_ingest.retrieval import RetrievalError

if TYPE_CHECKING:
    from argparse import _SubParsersAction

    from meridian_ingest.imagery.search import SceneSearch

__all__ = ["add_imagery", "run_imagery"]


def add_imagery(commands: _SubParsersAction[argparse.ArgumentParser]) -> None:
    """Add ``imagery`` and its verbs to the command tree."""
    imagery = commands.add_parser(
        "imagery", help="open imagery of the published areas (opens a socket)"
    )
    verbs = imagery.add_subparsers(dest="imagery_command", required=True)
    search = verbs.add_parser(
        "search", help="find and keep the clearest recent scene of each area"
    )
    search.add_argument(
        "--area", type=int, default=None, help="one published area id; default all"
    )
    search.add_argument(
        "--source",
        dest="imagery_source",
        default=None,
        help="one imagery source; default [imagery] sources in the settings file",
    )
    search.add_argument(
        "--summary",
        type=Path,
        default=None,
        help="the published areas-summary-1 file; default [imagery] summary",
    )


def run_imagery(args: argparse.Namespace, settings: IngestSettings) -> int:
    """Run one ``imagery`` verb."""
    try:
        require_imagery()
    except ImageryUnavailableError as exc:
        return refuse(f"meridian-ingest imagery: {exc}")
    return _search(args, settings)


def _search(args: argparse.Namespace, settings: IngestSettings) -> int:
    """Search each chosen source for each published area's clearest scene."""
    summary = args.summary or settings.imagery.summary
    if summary is None:
        return refuse(
            "meridian-ingest imagery search: name the published areas file with "
            "--summary or [imagery] summary; only a published area is imaged (D-326)"
        )
    named = (
        (args.imagery_source,)
        if args.imagery_source is not None
        else settings.imagery.sources
    )
    if not named:
        return refuse(
            "meridian-ingest imagery search: no imagery source is turned on; name "
            "one with --source or list them in [imagery] sources (D-220)"
        )
    try:
        sources = tuple(imagery_source(one) for one in named)
        areas = read_published_areas(summary).chosen(args.area)
    except (UnknownImagerySourceError, MalformedImageryError, LookupError) as exc:
        return refuse(f"meridian-ingest imagery search: {exc}")
    now = datetime.now(tz=UTC)
    failed = False
    for source in sources:
        if not _credited(source):
            failed = True
            continue
        failed = not _search_source(source, areas, settings, now) or failed
    return 1 if failed else 0


def _credited(source: ImagerySource) -> bool:
    """Whether the source is credited where it says, before any socket."""
    check = check_attribution(source.descriptor)
    if check.refuses:
        warn(
            f"meridian-ingest imagery: refusing {source.source_id} — {check.file} "
            f"does not mention {source.descriptor.attribution_entry!r}. A source "
            "is credited before its first retrieval (D-134)."
        )
        return False
    if check.file is None:
        warn(
            f"meridian-ingest imagery: no ATTRIBUTION.md above {Path.cwd()}, so "
            f"{source.source_id}'s credit could not be checked here."
        )
    return True


def _search_source(
    source: ImagerySource,
    areas: tuple[PublishedArea, ...],
    settings: IngestSettings,
    now: datetime,
) -> bool:
    """Every area, one source, one budget; one area's failure stops no other."""
    # Imported here, not above: these two need the [imagery] extra, which
    # run_imagery has just checked for, and every other verb runs without it.
    from meridian_ingest.imagery.pixels import (  # noqa: PLC0415
        RemotePixels,
        WindowError,
    )
    from meridian_ingest.imagery.search import (  # noqa: PLC0415
        SearchContext,
        search_area,
    )

    agent = _user_agent(settings)
    budget = RequestBudget(settings.imagery.request_budget)
    finished = True
    with HttpRetriever(
        open_client(agent, settings.timeout_s), budget, settings.retry
    ) as retriever:
        context = SearchContext(
            retriever=retriever,
            pixels=RemotePixels(agent, settings.timeout_s),
            store=RawStore(settings.raw_root),
            budget=budget,
            now=now,
        )
        for area in areas:
            try:
                _report(search_area(source, area, context))
            except (RetrievalError, WindowError, MalformedImageryError) as exc:
                warn(
                    f"meridian-ingest imagery: area {area.area_id}, "
                    f"{source.source_id}: {exc}"
                )
                finished = False
    return finished


def _report(found: SceneSearch) -> None:
    """One area's search, as an operator reads it."""
    area, source = found.area, found.source
    say(
        f"area {area.area_id} {area.label} — {source.source_id} ({source.source_class})"
    )
    if found.search_path is not None:
        say(f"    search: {found.search_path}")
    for one in found.considered:
        cloud = (
            "cloud not counted"
            if one.cloud_fraction is None
            else f"cloud {one.cloud_fraction:.1%} over the area"
        )
        scene = (
            "" if one.scene_cloud is None else f", {one.scene_cloud:.0f}% scene-wide"
        )
        verdict = "" if one.refused is None else f" — refused: {one.refused}"
        mark = "*" if one is found.chosen else " "
        when = f"{one.acquired_at:%Y-%m-%dT%H:%MZ}"
        say(f"  {mark} {when} {one.key}: {cloud}{scene}{verdict}")
    if found.chosen is None:
        say(f"    no scene: {found.skipped}")
    for path in found.windows:
        say(f"    kept: {path}")
    for note in found.notes:
        say(f"    note: {note}")


def _user_agent(settings: IngestSettings) -> str:
    """Who we are, plus a contact when the settings file gave one."""
    if settings.contact is None:
        return USER_AGENT
    return f"{USER_AGENT} <{settings.contact}>"
