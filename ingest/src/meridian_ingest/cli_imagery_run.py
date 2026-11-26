"""``meridian-ingest imagery refresh | render | list | verify`` — publishing scenes.

- ``refresh`` searches every published area in every enabled source, renders
  each area's clearest scene from what the search kept, and publishes it. One
  area's or one source's failure stops no other. With ``--interval`` it runs
  on a timer, as the ``ingest-imagery`` service does once a day.
- ``render`` publishes a scene from raw paths already held, with no socket:
  how a scene is made again by hand, or a synthetic one for a rehearsal.
- ``list`` prints the index.
- ``verify`` renders a published scene again from the inputs its manifest
  names, and says whether the same manifest comes out (rule 8).

Split from :mod:`.cli_imagery`, which builds the parser and runs ``search``.
Like it, this imports the imagery package's stdlib half at the top and the
half that decodes only once a verb that needs it runs (D-371).

Reference: docs/DECISIONS.md D-134, D-220, D-326, D-372, D-375.
"""

from __future__ import annotations

import argparse
import shutil
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from meridian_ingest.cli_fetch import check_attribution
from meridian_ingest.config import IngestSettings
from meridian_ingest.console import refuse, say, warn
from meridian_ingest.http_retriever import USER_AGENT, HttpRetriever, open_client
from meridian_ingest.imagery.areas import read_published_areas
from meridian_ingest.imagery.model import (
    ImageryManifest,
    MalformedImageryError,
    manifest_sha256,
)
from meridian_ingest.imagery.publish import (
    PublishError,
    damaged_files,
    publish_scene,
    read_index,
    read_manifest,
    scratch_directory,
)
from meridian_ingest.imagery.sources import ImagerySource
from meridian_ingest.politeness import RequestBudget
from meridian_ingest.raw_store import RawStore, RawStoreError
from meridian_ingest.retrieval import RetrievalError

if TYPE_CHECKING:
    from meridian_ingest.imagery.build import SceneRequest
    from meridian_ingest.imagery.search import SceneSearch, SearchContext

__all__ = [
    "credited",
    "report_search",
    "run_list",
    "run_refresh",
    "run_render",
    "run_verify",
    "searching",
]

HEARTBEAT_S = 60
"""How often a waiting ``refresh --interval`` touches its ``--alive`` file."""

_RAW_KINDS = ("catalogue", "classification", "scene", "firms", "gazetteer")
"""The inputs a manifest names by raw path, which a verify reads again."""


def run_refresh(
    args: argparse.Namespace,
    settings: IngestSettings,
    sources: tuple[ImagerySource, ...],
    summary: Path,
) -> int:
    """Search, render and publish, once or every ``--interval`` seconds."""
    root = settings.imagery.published
    if root is None:
        return refuse(_NO_PUBLISHED)
    alive: Path | None = args.alive

    def beat() -> None:
        if alive is not None:
            alive.touch()

    while True:
        beat()
        failed = refresh_round(settings, sources, summary, args.area, beat=beat)
        if args.interval is None:
            return 1 if failed else 0
        deadline = time.monotonic() + max(1, args.interval)
        while (left := deadline - time.monotonic()) > 0:
            beat()
            time.sleep(min(HEARTBEAT_S, left))


def refresh_round(  # noqa: PLR0913 — the settings, what to image, and the beat
    settings: IngestSettings,
    sources: tuple[ImagerySource, ...],
    summary: Path,
    area_id: int | None,
    *,
    now: datetime | None = None,
    beat: Callable[[], None] = lambda: None,
) -> bool:
    """One round over every area and source. Returns whether any failed."""
    from meridian_ingest.imagery.build import SceneRequest  # noqa: PLC0415
    from meridian_ingest.imagery.search import search_area  # noqa: PLC0415

    at = now or datetime.now(tz=UTC)
    try:
        published = read_published_areas(summary)
        areas = published.chosen(area_id)
    except (MalformedImageryError, LookupError) as exc:
        warn(f"meridian-ingest imagery refresh: {exc}")
        return True
    say(f"imagery refresh: {len(areas)} areas × {len(sources)} sources at {at:%FT%TZ}")
    failed = False
    for source in sources:
        if not credited(source):
            failed = True
            continue
        with searching(settings, at) as context:
            for area in areas:
                beat()
                try:
                    found = search_area(source, area, context)
                    report_search(found)
                    if found.chosen is None:
                        continue
                    request = SceneRequest(
                        area=area,
                        published=published,
                        summary_path=summary,
                        raw_paths=_kept(found),
                        gazetteer=settings.imagery.gazetteers.get(area.area_id),
                    )
                    _publish(settings, request, at)
                except _FAILURES as exc:
                    warn(f"imagery: area {area.area_id}, {source.source_id}: {exc}")
                    failed = True
    return failed


def run_render(
    args: argparse.Namespace, settings: IngestSettings, summary: Path
) -> int:
    """Publish one scene from raw paths already held; no socket is opened."""
    from meridian_ingest.imagery.build import SceneRequest  # noqa: PLC0415

    if settings.imagery.published is None:
        return refuse(_NO_PUBLISHED)
    try:
        published = read_published_areas(summary)
        (area,) = published.chosen(args.area)
        request = SceneRequest(
            area=area,
            published=published,
            summary_path=summary,
            raw_paths=tuple(args.raw_paths),
            gazetteer=settings.imagery.gazetteers.get(area.area_id),
        )
        _publish(settings, request, datetime.now(tz=UTC))
    except _FAILURES as exc:
        return refuse(f"meridian-ingest imagery render: {exc}")
    return 0


def run_list(settings: IngestSettings) -> int:
    """Print every scene the index lists, newest acquisition first."""
    root = settings.imagery.published
    if root is None:
        return refuse(_NO_PUBLISHED)
    try:
        index = read_index(root)
    except MalformedImageryError as exc:
        return refuse(f"meridian-ingest imagery list: {exc}")
    if not index.scenes:
        say(f"no scene is published in {root}")
    for one in index.scenes:
        say(
            f"{one.hash}  area {one.area_id}  {one.source_id:<15} "
            f"acquired {one.acquired_at:%FT%H:%MZ}  published "
            f"{one.published_at:%FT%H:%MZ}  {one.size_bytes / 1e6:.1f} MB"
        )
    return 0


def run_verify(
    args: argparse.Namespace, settings: IngestSettings, summary: Path
) -> int:
    """Render a published scene again from its inputs and compare the manifests."""
    root = settings.imagery.published
    if root is None:
        return refuse(_NO_PUBLISHED)
    verb = "meridian-ingest imagery verify"
    try:
        held = read_manifest(root, args.hash)
        again = _rebuilt(held, settings, summary)
    except _FAILURES as exc:
        return refuse(f"{verb}: {args.hash}: {exc}")
    damaged = damaged_files(root, args.hash, held)
    for path in damaged:
        warn(f"{verb}: {args.hash}/{path} no longer matches its manifest")
    if manifest_sha256(again) != manifest_sha256(held):
        before, after = held.to_json(), again.to_json()
        differ = sorted(key for key, value in after.items() if before[key] != value)
        return refuse(f"{verb}: {args.hash} rendered again differs in {differ}")
    say(f"{args.hash}: rendered again from its inputs to the same manifest")
    return 1 if damaged else 0


def _rebuilt(
    held: ImageryManifest, settings: IngestSettings, summary: Path
) -> ImageryManifest:
    """The scene made again from the inputs its manifest names, and no others.

    Raises:
        LookupError: ``summary`` is not the file the scene was made from, or
            no longer publishes its area.
    """
    from meridian_ingest.imagery.build import (  # noqa: PLC0415
        PinnedStore,
        SceneRequest,
        build_scene,
    )

    published = read_published_areas(summary)
    stated = [one for one in held.inputs if one.kind == "areas_summary"]
    if [one.sha256 for one in stated] != [published.file_sha256]:
        report = stated[0].ref[:12] if stated else "unknown"
        message = (
            f"it was made from the areas summary of report {report}, and {summary} "
            "is not that file. Write it again from that report with `meridian "
            "regions summary` and name it with --summary"
        )
        raise LookupError(message)
    (area,) = published.chosen(held.area_id)
    gazetteers = [one.ref for one in held.inputs if one.kind == "gazetteer"]
    request = SceneRequest(
        area=area,
        published=published,
        summary_path=summary,
        raw_paths=tuple(
            one.ref
            for one in held.inputs
            if one.kind in ("catalogue", "classification", "scene")
        ),
        gazetteer=(
            gazetteers[0].split("/")[0]
            if gazetteers
            else settings.imagery.gazetteers.get(area.area_id)
        ),
    )
    store = PinnedStore(
        settings.raw_root,
        (one.ref for one in held.inputs if one.kind in _RAW_KINDS),
    )
    with tempfile.TemporaryDirectory(prefix="meridian-verify-") as scratch:
        return build_scene(store, request, Path(scratch) / "scene")


# --- shared with ``search`` -------------------------------------------------------


def credited(source: ImagerySource) -> bool:
    """Whether the source is credited where it says, before any socket (D-134)."""
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


@contextmanager
def searching(settings: IngestSettings, now: datetime) -> Iterator[SearchContext]:
    """One source's search context: one client and one budget, for every area."""
    from meridian_ingest.imagery.pixels import RemotePixels  # noqa: PLC0415
    from meridian_ingest.imagery.search import SearchContext  # noqa: PLC0415

    agent = (
        USER_AGENT if settings.contact is None else f"{USER_AGENT} <{settings.contact}>"
    )
    budget = RequestBudget(settings.imagery.request_budget)
    with HttpRetriever(
        open_client(agent, settings.timeout_s), budget, settings.retry
    ) as retriever:
        yield SearchContext(
            retriever=retriever,
            pixels=RemotePixels(agent, settings.timeout_s),
            store=RawStore(settings.raw_root),
            budget=budget,
            now=now,
        )


def report_search(found: SceneSearch) -> None:
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


# --- the steps ---------------------------------------------------------------------

_NO_PUBLISHED = (
    "meridian-ingest imagery: name where scenes are published with [imagery] "
    "published, the imagery directory under PUBLISHED_DIR (D-375)"
)


_FAILURES: tuple[type[Exception], ...] = (
    RetrievalError,
    MalformedImageryError,
    PublishError,
    RawStoreError,
    LookupError,
    ValueError,
)
"""What one area's scene may end in. ``WindowError``, ``RenderError`` and
``BuildError`` are ``ValueError``s, so they are caught without importing the
half of the package that needs the extra."""


def _kept(found: SceneSearch) -> tuple[str, ...]:
    """What the scene is made from: the answer, the cloud layers, the picture."""
    chosen = found.chosen
    assert chosen is not None
    answer = () if found.search_path is None else (found.search_path,)
    return (*answer, *chosen.raw_paths, *found.windows)


def _publish(settings: IngestSettings, request: SceneRequest, now: datetime) -> None:
    from meridian_ingest.imagery.build import build_scene  # noqa: PLC0415

    root = settings.imagery.published
    assert root is not None
    scratch = scratch_directory(root)
    store = RawStore(settings.raw_root)
    try:
        manifest = build_scene(store, request, scratch)
    except BaseException:
        if scratch.exists():
            shutil.rmtree(scratch)
        raise
    done = publish_scene(
        root, scratch, manifest, now=now, max_bytes=settings.imagery.max_bytes
    )
    say(f"    {done.said}: {root / done.hash}")
    for name in done.removed:
        say(f"    removed: {root / name}, which the index no longer lists")
