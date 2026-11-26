"""A scene made from raw paths alone: its files, its markers, and its manifest.

``build_scene`` is the one function a published scene comes from, whether a
daily refresh has just searched for it or ``imagery verify`` is making it
again months later. Its input is a list of raw paths and the area; it sorts
them by what the raw store says each is:

- ``tile`` payloads are the picture: a STAC source's display windows, or
  GIBS's tiles;
- a ``data`` window is a classification layer, which the cloud fraction is
  counted from again here, never carried over from the search;
- other ``data`` is the catalogue's answer, which names the satellite.

So everything the manifest states is computed from what the raw store holds:
the pixels, the cloud share, the platform, the markers and the caption. The
manifest lists every one of those inputs by its digest, and rendering the
same inputs again gives the same manifest, byte for byte (rule 8).

**A verify sees only what the manifest names.** The markers are chosen from
the raw store — the newest FIRMS file of each day, the newest gazetteer — and
months later there are newer ones. :class:`PinnedStore` is the raw store with
every other artefact out of sight, so the same choice is made again.

Reference: docs/DECISIONS.md D-133, D-141, D-372, D-373, D-374, D-375.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from meridian_ingest.imagery.annotate import SceneMarkers, annotate_scene
from meridian_ingest.imagery.areas import PublishedArea, PublishedAreas
from meridian_ingest.imagery.caption import (
    CAPTION_TEMPLATE,
    TEMPLATE_SHA256,
    CaptionFacts,
    caption,
)
from meridian_ingest.imagery.cloud import CloudCount, count_cloud
from meridian_ingest.imagery.model import (
    ImageryManifest,
    Input,
    InputKind,
    MalformedImageryError,
)
from meridian_ingest.imagery.pixels import LocalPixels, WindowError, read_window
from meridian_ingest.imagery.render import RenderedScene, render_composed
from meridian_ingest.imagery.scene import RenderError, compose
from meridian_ingest.imagery.sources import (
    GibsSource,
    ImagerySource,
    StacSource,
    cloud_method_for,
    credit_for,
    imagery_source,
    is_simulated,
    platform_name,
)
from meridian_ingest.imagery.stac import parse_search
from meridian_ingest.imagery.stored import MEDIA_TYPE, read_scene_window
from meridian_ingest.raw_store import RawStore, StoredArtefact

__all__ = ["BuildError", "PinnedStore", "SceneRequest", "build_scene"]


class BuildError(ValueError):
    """The raw paths given cannot be made into a published scene."""


class PinnedStore(RawStore):
    """A raw store that lists only the artefacts it was given.

    ``read`` still reaches any path, so a manifest's inputs are read as they
    are; ``scan`` lists only the pinned ones, so a marker is chosen from what
    the scene was first made from and not from what has arrived since.
    """

    def __init__(self, root: Path, pinned: Iterable[str]) -> None:
        """Point at ``root`` and list only ``pinned`` from it."""
        super().__init__(root)
        self._pinned = frozenset(pinned)

    def scan(self, source_id: str) -> tuple[str, ...]:
        """The pinned records of one source, in name order."""
        return tuple(one for one in super().scan(source_id) if one in self._pinned)


@dataclass(frozen=True, slots=True)
class SceneRequest:
    """One scene to make: of which area, from which raw paths."""

    area: PublishedArea
    published: PublishedAreas
    summary_path: Path
    """The ``areas-summary-1`` file ``published`` was read from."""

    raw_paths: tuple[str, ...]
    gazetteer: str | None = None
    """The area's chosen gazetteer, where it overrides the size rule."""


@dataclass(frozen=True, slots=True)
class _Parts:
    source: ImagerySource
    catalogue: tuple[StoredArtefact, ...]
    classification: tuple[StoredArtefact, ...]
    display: tuple[StoredArtefact, ...]


def build_scene(
    store: RawStore, request: SceneRequest, directory: Path
) -> ImageryManifest:
    """Render the scene into ``directory`` and return its manifest.

    ``directory`` must not exist yet; the caller publishes it, or discards it.

    Raises:
        BuildError: The raw paths are of several sources, lack a part the
            source needs, or cannot be made into a picture.
        MalformedImageryError: A record read on the way breaks a rule.
    """
    parts = _parts(store, request.raw_paths)
    area = request.area
    try:
        scene = compose(store, [one.raw_path for one in parts.display], area.bounds)
    except RenderError as exc:
        raise BuildError(str(exc)) from exc
    markers = annotate_scene(
        store,
        request.summary_path,
        request.published,
        area,
        acquired_at=scene.acquired_at,
        gazetteer=request.gazetteer,
    )
    rendered = render_composed(
        scene, directory, markers=markers.annotations, credits=markers.credits
    )
    cloud = _cloud(parts, area)
    platform = _platform(parts)
    credit = credit_for(parts.source, scene.acquired_at.year)
    text = caption(
        _facts(
            area,
            parts.source,
            rendered,
            markers,
            cloud=cloud,
            platform=platform,
            credit=credit,
        )
    )
    return ImageryManifest(
        area_id=area.area_id,
        area_label=area.label,
        source_id=parts.source.source_id,
        source_class=parts.source.source_class,
        platform=platform,
        instrument=parts.source.instrument,
        acquired_at=rendered.acquired_at,
        bounds=rendered.bounds,
        pixel_size_deg=rendered.pixel_size_deg,
        resolution_m=rendered.resolution_m,
        cloud_fraction=None if cloud is None else cloud.fraction,
        cloud_method=cloud_method_for(parts.source),
        rendering=parts.source.rendering,
        zoom_min=rendered.zoom_min,
        zoom_max=rendered.zoom_max,
        credit=credit,
        licence=parts.source.descriptor.licence,
        caption=text,
        caption_template=CAPTION_TEMPLATE,
        annotations=markers.annotations,
        inputs=(
            *_inputs("catalogue", parts.catalogue),
            *_inputs("classification", parts.classification),
            *_inputs("scene", parts.display),
            *markers.inputs,
            Input("template", CAPTION_TEMPLATE, TEMPLATE_SHA256),
        ),
        files=rendered.files,
        simulated=is_simulated(parts.source),
    )


def _parts(store: RawStore, raw_paths: Sequence[str]) -> _Parts:
    """The raw paths, read and sorted into what each is."""
    if len(set(raw_paths)) != len(raw_paths):
        message = "a raw path is named more than once"
        raise BuildError(message)
    stored = [store.read(path) for path in sorted(raw_paths)]
    sources = {one.manifest.provenance.source_id for one in stored}
    if len(sources) != 1:
        message = f"one scene is made from one source, not {sorted(sources) or 'none'}"
        raise BuildError(message)
    source = imagery_source(sources.pop())
    display = tuple(one for one in stored if _kind(one) == "tile")
    classification = tuple(one for one in stored if _kind(one) == "classification")
    catalogue = tuple(one for one in stored if _kind(one) == "catalogue")
    if not display:
        message = f"a {source.source_id} scene needs its display windows or tiles"
        raise BuildError(message)
    if isinstance(source, StacSource):
        if len(catalogue) != 1:
            message = (
                f"a {source.source_id} scene is made with the one catalogue answer "
                f"it was chosen from, not {len(catalogue)}"
            )
            raise BuildError(message)
        if not classification:
            message = (
                f"a {source.source_id} scene needs the {source.classification_asset} "
                "windows its cloud is counted from (D-372)"
            )
            raise BuildError(message)
    elif catalogue or classification:
        message = "a GIBS view is made from its tiles alone; it has no catalogue"
        raise BuildError(message)
    return _Parts(source, catalogue, classification, display)


def _kind(one: StoredArtefact) -> str:
    provenance = one.manifest.provenance
    if provenance.payload_kind == "tile":
        return "tile"
    return "classification" if provenance.media_type == MEDIA_TYPE else "catalogue"


def _cloud(parts: _Parts, area: PublishedArea) -> CloudCount | None:
    """The cloud over the box, counted again from the stored classification windows.

    Each window is read through the same door a search reads the archive
    through, pointed at the stored file, so the box is cut on the same grid and
    the count is the one the search made. Granules are placed in item-id order,
    as the search placed them.
    """
    source = parts.source
    if not isinstance(source, StacSource):
        return None
    records = sorted(
        ((read_scene_window(one.artefact), one) for one in parts.classification),
        key=lambda pair: pair[0].item_id,
    )
    if any(record.asset != source.classification_asset for record, _ in records):
        message = f"a classification window is not {source.classification_asset}"
        raise BuildError(message)
    try:
        windows = [
            read_window(
                LocalPixels({record.asset_href: one.artefact}),
                record.asset_href,
                area.bounds,
                overview_level=0,
            )
            for record, one in records
        ]
        count = count_cloud(source.classifier, windows)
    except WindowError as exc:
        raise BuildError(str(exc)) from exc
    if count.fraction is None or not count.covers:
        message = "the classification windows do not cover the box with valid pixels"
        raise BuildError(message)
    return count


def _platform(parts: _Parts) -> str:
    """The satellite, as the catalogue answer names the items drawn."""
    source = parts.source
    if isinstance(source, GibsSource):
        return source.platform
    if source.simulated:
        return "No satellite (simulated)"
    drawn = {read_scene_window(one.artefact).item_id for one in parts.display}
    body = parts.catalogue[0].artefact.read_bytes()
    candidates, _, _ = parse_search(body, source)
    platforms = {one.platform for one in candidates if one.item_id in drawn}
    named = {one.item_id for one in candidates if one.item_id in drawn}
    if named != drawn:
        missing = ", ".join(sorted(drawn - named))
        message = f"the catalogue answer does not name {missing}"
        raise BuildError(message)
    if len(platforms) != 1:
        message = f"one scene comes from one satellite, not {sorted(platforms)}"
        raise MalformedImageryError(message)
    return platform_name(platforms.pop())


def _facts(  # noqa: PLR0913 — a caption states each of these
    area: PublishedArea,
    source: ImagerySource,
    rendered: RenderedScene,
    markers: SceneMarkers,
    *,
    cloud: CloudCount | None,
    platform: str,
    credit: str,
) -> CaptionFacts:
    places = markers.places
    return CaptionFacts(
        area_label=area.label,
        source_class=source.source_class,
        platform=platform,
        instrument=source.instrument,
        acquired_at=rendered.acquired_at,
        bounds=rendered.bounds,
        resolution_m=rendered.resolution_m,
        cloud_fraction=None if cloud is None else cloud.fraction,
        cloud_method=cloud_method_for(source),
        credit=credit,
        changes=markers.changes,
        fire_days=markers.fires.days,
        fire_days_held=markers.fires.held,
        fires_detected=markers.fires.detected,
        fires_shown=len(markers.fires.annotations),
        gazetteer=places.named_by,
        places_named_by=None if places.raw_path is None else places.named_by,
        places=len(places.places),
        simulated=is_simulated(source),
    )


def _inputs(kind: InputKind, stored: Sequence[StoredArtefact]) -> tuple[Input, ...]:
    return tuple(Input(kind, one.raw_path, one.manifest.sha256.hex()) for one in stored)
