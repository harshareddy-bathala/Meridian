"""``meridian-ingest imagery`` — open imagery of the published areas.

``imagery search`` finds the clearest recent scene of each published area in
each chosen source, keeps what it read in the raw store, and prints each
choice with its reasons. It opens a socket, as ``fetch`` does, and it is held
to the same rules before it does: a source is refused unless its attribution
entry is written down (D-134), a run has a request budget, and every request
says who we are. ``refresh``, ``render``, ``list`` and ``verify`` publish what
a search kept, and are in :mod:`.cli_imagery_run`.

**The imagery libraries are imported when a verb runs, not before.** This
module is imported by ``meridian_ingest.cli`` on every invocation, and an
installation without the ``[imagery]`` extra must still run ``fetch`` and
``load``. What it imports at the top is the package's stdlib half — the areas,
the records, the sources — and the reader and the search arrive only after a
missing extra has been refused by name (D-371).

Reference: docs/DECISIONS.md D-134, D-220, D-326, D-371, D-372, D-375.
"""

from __future__ import annotations

import argparse
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from meridian_ingest.cli_imagery_run import (
    NO_PUBLISHED,
    credited,
    publish_request,
    report_search,
    run_list,
    run_refresh,
    run_render,
    run_verify,
    searching,
)
from meridian_ingest.config import IngestSettings
from meridian_ingest.console import refuse, warn
from meridian_ingest.imagery import ImageryUnavailableError, require_imagery
from meridian_ingest.imagery.areas import PublishedArea, read_published_areas
from meridian_ingest.imagery.model import MalformedImageryError
from meridian_ingest.imagery.sources import (
    ImagerySource,
    UnknownImagerySourceError,
    imagery_source,
    is_simulated,
)
from meridian_ingest.raw_store import RawStore
from meridian_ingest.retrieval import RetrievalError

if TYPE_CHECKING:
    from argparse import _SubParsersAction

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
    _areas_and_sources(search)
    refresh = verbs.add_parser(
        "refresh", help="search, render and publish each area's clearest scene"
    )
    _areas_and_sources(refresh)
    refresh.add_argument(
        "--interval",
        type=int,
        default=None,
        help="seconds between rounds; default one round, then exit",
    )
    refresh.add_argument(
        "--alive",
        type=Path,
        default=None,
        help="a file touched every minute while running, for a healthcheck",
    )
    render = verbs.add_parser(
        "render", help="publish one scene from raw paths already held (no socket)"
    )
    render.add_argument("--area", type=int, required=True, help="the published area")
    _summary(render)
    render.add_argument("raw_paths", nargs="+", help="the scene's raw paths")
    demo = verbs.add_parser(
        "demo",
        help="publish a simulated scene of each area, for a rehearsal (no socket)",
    )
    demo.add_argument(
        "--area", type=int, default=None, help="one published area id; default all"
    )
    _summary(demo)
    verbs.add_parser("list", help="print every published scene, newest first")
    verify = verbs.add_parser(
        "verify", help="render a published scene again from its inputs and compare"
    )
    verify.add_argument("hash", help="the scene's twelve-digit name")
    _summary(verify)


def _areas_and_sources(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--area", type=int, default=None, help="one published area id; default all"
    )
    parser.add_argument(
        "--source",
        dest="imagery_source",
        default=None,
        help="one imagery source; default [imagery] sources in the settings file",
    )
    _summary(parser)


def _summary(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--summary",
        type=Path,
        default=None,
        help="the published areas-summary-1 file; default [imagery] summary",
    )


def run_imagery(args: argparse.Namespace, settings: IngestSettings) -> int:
    """Run one ``imagery`` verb."""
    verb = args.imagery_command
    if verb == "list":
        return run_list(settings)
    try:
        require_imagery()
    except ImageryUnavailableError as exc:
        return refuse(f"meridian-ingest imagery: {exc}")
    summary = args.summary or settings.imagery.summary
    if summary is None:
        return refuse(
            f"meridian-ingest imagery {verb}: name the published areas file with "
            "--summary or [imagery] summary; only a published area is imaged (D-326)"
        )
    return _VERBS[verb](args, settings, summary)


def _refresh(args: argparse.Namespace, settings: IngestSettings, summary: Path) -> int:
    sources = _sources(args, settings, "refresh")
    if isinstance(sources, int):
        return sources
    return run_refresh(args, settings, sources, summary)


def _sources(
    args: argparse.Namespace, settings: IngestSettings, verb: str
) -> tuple[ImagerySource, ...] | int:
    """The sources a searching verb uses, or the refusal's exit code."""
    named = (
        (args.imagery_source,)
        if args.imagery_source is not None
        else settings.imagery.sources
    )
    if not named:
        return refuse(
            f"meridian-ingest imagery {verb}: no imagery source is turned on; name "
            "one with --source or list them in [imagery] sources (D-220)"
        )
    try:
        sources = tuple(imagery_source(one) for one in named)
    except UnknownImagerySourceError as exc:
        return refuse(f"meridian-ingest imagery {verb}: {exc}")
    simulated = [one.source_id for one in sources if is_simulated(one)]
    if simulated:
        return refuse(
            f"meridian-ingest imagery {verb}: {', '.join(simulated)} is the "
            "demonstration's synthetic archive, which no network holds; "
            "`imagery demo` writes it (D-378)"
        )
    return sources


def _search(args: argparse.Namespace, settings: IngestSettings, summary: Path) -> int:
    """Search each chosen source for each published area's clearest scene."""
    sources = _sources(args, settings, "search")
    if isinstance(sources, int):
        return sources
    try:
        areas = read_published_areas(summary).chosen(args.area)
    except (MalformedImageryError, LookupError) as exc:
        return refuse(f"meridian-ingest imagery search: {exc}")
    now = datetime.now(tz=UTC)
    failed = False
    for source in sources:
        if not credited(source):
            failed = True
            continue
        failed = not _search_source(source, areas, settings, now) or failed
    return 1 if failed else 0


def _search_source(
    source: ImagerySource,
    areas: tuple[PublishedArea, ...],
    settings: IngestSettings,
    now: datetime,
) -> bool:
    """Every area, one source, one budget; one area's failure stops no other."""
    # Imported here, not above: these two need the [imagery] extra, which
    # run_imagery has just checked for, and every other verb runs without it.
    from meridian_ingest.imagery.pixels import WindowError  # noqa: PLC0415
    from meridian_ingest.imagery.search import search_area  # noqa: PLC0415

    finished = True
    with searching(settings, now) as context:
        for area in areas:
            try:
                report_search(search_area(source, area, context))
            except (RetrievalError, WindowError, MalformedImageryError) as exc:
                warn(
                    f"meridian-ingest imagery: area {area.area_id}, "
                    f"{source.source_id}: {exc}"
                )
                finished = False
    return finished


def _demo(args: argparse.Namespace, settings: IngestSettings, summary: Path) -> int:
    """Publish a simulated scene of each published area, with no socket (D-378).

    The scene is searched, counted, rendered and published by the same code a
    real one is, from a synthetic archive written to a scratch directory, and
    is labelled simulated at every layer.
    """
    from meridian_ingest.imagery.build import SceneRequest  # noqa: PLC0415
    from meridian_ingest.imagery.demo import demo_search  # noqa: PLC0415

    if settings.imagery.published is None:
        return refuse(NO_PUBLISHED)
    now = datetime.now(tz=UTC)
    try:
        published = read_published_areas(summary)
        areas = published.chosen(args.area)
    except (MalformedImageryError, LookupError) as exc:
        return refuse(f"meridian-ingest imagery demo: {exc}")
    failed = False
    for area in areas:
        with tempfile.TemporaryDirectory(prefix="meridian-demo-") as scratch:
            try:
                found = demo_search(
                    RawStore(settings.raw_root), area, now, Path(scratch)
                )
                report_search(found)
                if found.chosen is None:
                    failed = True
                    continue
                request = SceneRequest(
                    area=area,
                    published=published,
                    summary_path=summary,
                    raw_paths=(
                        *((found.search_path,) if found.search_path else ()),
                        *found.chosen.raw_paths,
                        *found.windows,
                    ),
                )
                publish_request(settings, request, now)
            except (RetrievalError, ValueError, OSError) as exc:
                warn(f"meridian-ingest imagery demo: area {area.area_id}: {exc}")
                failed = True
    return 1 if failed else 0


_VERBS: dict[str, Callable[[argparse.Namespace, IngestSettings, Path], int]] = {
    "demo": _demo,
    "search": _search,
    "refresh": _refresh,
    "render": run_render,
    "verify": run_verify,
}
