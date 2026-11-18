"""Find the clearest recent scene over each published area, and keep what was read.

For a searchable source, one area is one search. The search answer is kept in
the raw store as it arrived. Its items are grouped into passes over the box,
the least cloudy (by the catalogue's scene-wide figure) are read, and each is
scored by the share of the box its own classification layer calls cloud
(:mod:`.cloud`). The clearest pass that covers the box wins, a tie going to the
newer. Its display windows are read and kept. Every classification window that
was counted is kept too, so the choice can be checked again from the raw store
alone (rule 8).

GIBS has no catalogue and no classification layer. Its daily view is the
previous UTC day's tiles over the box, kept as tiles, with no cloud fraction.

**What is kept, and as what.** The raw store's payload kinds are ``data`` and
``tile``, the two ``ingest_records`` is CHECKed against (0016). A search answer
and a classification window are ``data``: records a number is read from. A
display window and a GIBS tile are ``tile``: pictures, never sampled (D-133).
None of these sources is in the fetch registry, so ``load`` never visits them.

**What a run spends.** A search, a token where the archive needs one, and one
request from the budget per window read — GDAL makes a few range requests per
window, which the budget counts as one, because each window is one question
asked of the archive.

Reference: docs/DECISIONS.md D-133, D-141, D-326, D-372.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta

from meridian_ingest.adapters.public.common import version_from_headers
from meridian_ingest.adapters.public.gibs import BASE as GIBS_BASE
from meridian_ingest.adapters.public.gibs import tile_span_deg, tiles_covering
from meridian_ingest.extent import BoundingBox
from meridian_ingest.imagery.areas import PublishedArea
from meridian_ingest.imagery.cloud import CloudCount, count_cloud
from meridian_ingest.imagery.model import SceneWindow
from meridian_ingest.imagery.pixels import (
    OffSceneError,
    PixelReader,
    ReadWindow,
    WindowError,
    read_window,
)
from meridian_ingest.imagery.sources import GibsSource, ImagerySource, StacSource
from meridian_ingest.imagery.stac import (
    Acquisition,
    SceneCandidate,
    acquisitions,
    parse_search,
    search_artefact,
)
from meridian_ingest.imagery.stored import MEDIA_TYPE, window_bytes
from meridian_ingest.politeness import RequestBudget
from meridian_ingest.provenance import Provenance
from meridian_ingest.raw_store import RawStore
from meridian_ingest.retrieval import RemoteArtefact, RetrievalError, Retriever

__all__ = [
    "MAX_EVALUATED",
    "Considered",
    "SceneSearch",
    "SearchContext",
    "search_area",
]

MAX_EVALUATED = 6
"""Passes whose classification window is read, least cloudy first by the
catalogue. Six covers a month of Sentinel‑2 revisits that are not plainly
overcast, at a dozen requests an area."""


@dataclass(slots=True)
class SearchContext:
    """What one run searches with: the network seams, the store, and the clock.

    ``now`` is the instant the run asks about, fixed for the run, so every
    area's lookback ends at the same moment. ``clock`` stamps each artefact as
    it arrives.
    """

    retriever: Retriever
    pixels: PixelReader
    store: RawStore
    budget: RequestBudget
    now: datetime
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(tz=UTC))


@dataclass(frozen=True, slots=True)
class _Granule:
    """One asset's window, read from one granule of a pass."""

    candidate: SceneCandidate
    asset: str
    window: ReadWindow


@dataclass(frozen=True, slots=True)
class Considered:
    """One pass that was read, and what its classification layer said."""

    key: str
    acquired_at: datetime
    item_ids: tuple[str, ...]
    scene_cloud: float | None
    cloud: CloudCount | None
    refused: str | None
    """Why it cannot be chosen, or None when it can."""

    raw_paths: tuple[str, ...]
    """The classification windows it was counted from."""

    @property
    def cloud_fraction(self) -> float | None:
        """The share of the box behind cloud, or None if it was never counted."""
        return None if self.cloud is None else self.cloud.fraction


@dataclass(frozen=True, slots=True)
class SceneSearch:
    """One area's search of one source: what was considered and what was kept."""

    area: PublishedArea
    source: ImagerySource
    search_path: str | None
    considered: tuple[Considered, ...]
    chosen: Considered | None
    windows: tuple[str, ...]
    """The display windows (or tiles) of the chosen scene, in the raw store."""

    notes: tuple[str, ...]

    @property
    def skipped(self) -> str | None:
        """Why no scene was chosen, or None when one was."""
        if self.chosen is not None:
            return None
        if not self.considered:
            return "no scene in the lookback reaches the box under the cloud ceiling"
        return "no pass read covers the box with valid pixels"


def search_area(
    source: ImagerySource, area: PublishedArea, context: SearchContext
) -> SceneSearch:
    """Search one source for one area's clearest recent scene, and keep it.

    Raises:
        RetrievalError: A request could not be made, or the budget ran out.
        WindowError: The chosen scene's display windows could not be read.
        MalformedImageryError: The catalogue's answer is not a STAC answer.
    """
    if isinstance(source, GibsSource):
        return _gibs(source, area, context)
    return _stac(source, area, context)


def _stac(
    source: StacSource, area: PublishedArea, context: SearchContext
) -> SceneSearch:
    remote = search_artefact(source, area.bounds, context.now)
    search_path = _keep(context, source.source_id, remote)
    body = context.store.read(search_path).read_bytes()
    candidates, set_aside, full = parse_search(body, source)
    notes = list(set_aside)
    if full:
        notes.append("the answer was a full page, so older scenes were not seen")
    passes = acquisitions(candidates, area.bounds)
    if len(passes) > MAX_EVALUATED:
        unread = len(passes) - MAX_EVALUATED
        notes.append(f"{unread} cloudier passes were not read")
    if not passes:
        return SceneSearch(area, source, search_path, (), None, (), tuple(notes))
    pixels = context.pixels
    if source.token_url is not None:
        pixels = pixels.with_token(_token(context, source))
    considered = tuple(
        _consider(source, area, one, pixels, context) for one in passes[:MAX_EVALUATED]
    )
    chosen = _clearest(considered)
    windows: tuple[str, ...] = ()
    if chosen is not None:
        chosen_pass = next(one for one in passes if one.key == chosen.key)
        windows = _display(source, area, chosen_pass, pixels, context)
    return SceneSearch(
        area, source, search_path, considered, chosen, windows, tuple(notes)
    )


def _consider(
    source: StacSource,
    area: PublishedArea,
    one: Acquisition,
    pixels: PixelReader,
    context: SearchContext,
) -> Considered:
    """Read a pass's classification windows, count its cloud, and keep them."""
    asset = source.classification_asset
    ids = tuple(candidate.item_id for candidate in one.candidates)
    try:
        read = _windows(asset, area, one, pixels, context)
        count = count_cloud(source.classifier, [one.window for one in read])
    except WindowError as exc:
        return Considered(
            one.key, one.acquired_at, ids, one.scene_cloud, None, str(exc), ()
        )
    paths = tuple(_keep_window(context, source, area, one, "data") for one in read)
    refused = None
    if count.fraction is None:
        refused = "no valid pixel over the box"
    elif not count.covers:
        refused = f"leaves {count.nodata_share:.1%} of the box without data"
    return Considered(
        one.key, one.acquired_at, ids, one.scene_cloud, count, refused, paths
    )


def _display(
    source: StacSource,
    area: PublishedArea,
    one: Acquisition,
    pixels: PixelReader,
    context: SearchContext,
) -> tuple[str, ...]:
    """Read and keep the chosen pass's display windows, asset by asset."""
    kept: list[str] = []
    for asset in source.display_assets:
        kept.extend(
            _keep_window(context, source, area, granule, "tile")
            for granule in _windows(asset, area, one, pixels, context)
        )
    return tuple(kept)


def _windows(
    asset: str,
    area: PublishedArea,
    one: Acquisition,
    pixels: PixelReader,
    context: SearchContext,
) -> list[_Granule]:
    """One asset's window from each granule of a pass, all at one level.

    The first granule that reaches the box chooses the overview level; the
    rest are read at it, so their pixels share a grid. A granule whose data
    does not reach the box is passed over.

    Raises:
        OffSceneError: No granule of the pass reaches the box.
        WindowError: A window could not be read.
    """
    read: list[_Granule] = []
    level: int | None = None
    for candidate in one.candidates:
        context.budget.spend(f"{candidate.item_id}/{asset}")
        try:
            window = read_window(
                pixels, candidate.assets[asset], area.bounds, overview_level=level
            )
        except OffSceneError:
            continue
        level = window.overview_level
        read.append(_Granule(candidate, asset, window))
    if not read:
        message = f"no granule of {one.key} has {asset} pixels over the box"
        raise OffSceneError(message)
    return read


def _clearest(considered: tuple[Considered, ...]) -> Considered | None:
    """The least cloudy pass that covers the box; a tie goes to the newer."""
    eligible = [
        one
        for one in considered
        if one.refused is None and one.cloud_fraction is not None
    ]
    if not eligible:
        return None
    return min(
        eligible,
        key=lambda one: (one.cloud_fraction, -one.acquired_at.timestamp(), one.key),
    )


def _token(context: SearchContext, source: StacSource) -> str:
    """An anonymous read token, held in memory for this run and never written.

    Fetched through the retriever, so it is paid for and polite like any
    request, but its answer is never published to the raw store: the terms
    forbid passing a token on (D-371).
    """
    url = source.token_url
    if url is None:
        message = f"{source.source_id} has no token to ask for"
        raise RetrievalError(message)
    remote = RemoteArtefact(
        url=url,
        original_identifier=f"read token for {source.collection}",
        payload_kind="data",
    )
    retrieved = context.retriever.retrieve(remote)
    body = b"".join(retrieved.chunks)
    try:
        token = json.loads(body).get("token")
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
        token = None
    if not isinstance(token, str) or not token.strip():
        message = f"{url} gave no token"
        raise RetrievalError(message)
    return token.strip().lstrip("?")


def _gibs(
    source: GibsSource, area: PublishedArea, context: SearchContext
) -> SceneSearch:
    """The previous UTC day's tiles over the box: GIBS's daily view, kept as tiles."""
    day = (context.now.astimezone(UTC) - timedelta(days=1)).date()
    start = datetime.combine(day, time(), tzinfo=UTC)
    box = BoundingBox(*area.bounds.to_list())
    span = tile_span_deg(source.level)
    kept = []
    for row, col in tiles_covering(box, source.level):
        west, north = -180.0 + col * span, 90.0 - row * span
        remote = RemoteArtefact(
            url=(
                f"{GIBS_BASE}/{source.layer}/default/{day.isoformat()}/250m/"
                f"{source.level}/{row}/{col}.jpg"
            ),
            original_identifier=f"{source.layer}/{day.isoformat()}/{source.level}/{row}/{col}",
            payload_kind="tile",
            valid_from=start,
            valid_to=start + timedelta(days=1),
            spatial_extent={
                "west": west,
                "south": north - span,
                "east": west + span,
                "north": north,
            },
        )
        kept.append(_keep(context, source.source_id, remote))
    chosen = Considered(
        key=f"{source.layer} {day.isoformat()}",
        acquired_at=start,
        item_ids=(),
        scene_cloud=None,
        cloud=None,
        refused=None,
        raw_paths=(),
    )
    note = (
        "GIBS publishes no classification layer, so its daily view states no "
        "cloud fraction"
    )
    return SceneSearch(area, source, None, (chosen,), chosen, tuple(kept), (note,))


def _keep(context: SearchContext, source_id: str, remote: RemoteArtefact) -> str:
    """Fetch one artefact and publish it to the raw store unchanged.

    The retriever spends from the run's budget for each attempt, as it does
    for every fetch; the budget is the same object a window read spends from.
    """
    retrieved = context.retriever.retrieve(remote)
    provenance = Provenance(
        source_id=source_id,
        original_identifier=remote.original_identifier,
        source_version=version_from_headers(retrieved),
        payload_kind=remote.payload_kind,
        retrieved_at=context.clock(),
        media_type=retrieved.media_type,
        valid_from=remote.valid_from,
        valid_to=remote.valid_to,
        spatial_extent=remote.spatial_extent,
    )
    return context.store.publish(provenance, retrieved.chunks).raw_path


def _keep_window(
    context: SearchContext,
    source: StacSource,
    area: PublishedArea,
    granule: _Granule,
    payload_kind: str,
) -> str:
    """Publish one window, its record inside, to the raw store."""
    candidate, asset, window = granule.candidate, granule.asset, granule.window
    record = SceneWindow(
        source_id=source.source_id,
        item_id=candidate.item_id,
        asset=asset,
        asset_href=window.href,
        acquired_at=candidate.acquired_at,
        crs=window.crs,
        transform=window.transform,
        width=window.width,
        height=window.height,
        overview_level=window.overview_level,
        etag=None,
    )
    provenance = Provenance(
        source_id=source.source_id,
        original_identifier=f"{candidate.item_id}/{asset}",
        source_version=candidate.version,
        payload_kind=payload_kind,
        retrieved_at=context.clock(),
        media_type=MEDIA_TYPE,
        valid_from=candidate.acquired_at,
        valid_to=candidate.acquired_at,
        spatial_extent=dict(
            zip(("west", "south", "east", "north"), area.bounds.to_list(), strict=True)
        ),
    )
    data = window_bytes(window, record)
    return context.store.publish(provenance, [data]).raw_path
