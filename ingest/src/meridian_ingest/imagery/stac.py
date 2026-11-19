"""A STAC search over one area, and the scenes its answer offers.

The request is planned as a :class:`~meridian_ingest.retrieval.RemoteArtefact`,
so it goes through the same retriever, politeness and budget as every fetch,
and the answer is kept in the raw store exactly as it arrived (D-141, D-372).
Everything after that is reading a stored file: parsing it needs no network,
so a choice made once can be checked again offline.

**What the answer is trusted for.** Each item's id, time, platform, CRS and
asset addresses. Its scene-wide ``eo:cloud_cover`` only orders and pre-filters
candidates: it describes a whole 110 km granule, and an area of a few
kilometres can sit in the one clear corner of a cloudy scene, or under the one
cloud of a clear one. The fraction a scene is chosen by is counted over the
area from the product's own classification layer (:mod:`.cloud`).

**Granules of one acquisition are one scene.** A box on a granule edge is
covered by two items from the same pass, so candidates are grouped by
acquisition: Sentinel‑2's datatake, or Landsat's platform, day and path. And
by grid: a pass that crosses a UTM zone boundary is two scenes, one per zone,
because joining them would mean resampling one onto the other's grid, and a
stored window is the source's own pixels (D-372).

Stdlib only: a catalogue answer is a record, not a picture.

Reference: docs/DECISIONS.md D-133, D-141, D-372.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TypeVar
from urllib.parse import urlencode, urlsplit, urlunsplit

from meridian_ingest.imagery.model import Bounds, MalformedImageryError
from meridian_ingest.imagery.sources import StacSource
from meridian_ingest.retrieval import RemoteArtefact

__all__ = [
    "MAX_SCENE_CLOUD",
    "PAGE_LIMIT",
    "Acquisition",
    "SceneCandidate",
    "acquisitions",
    "parse_search",
    "search_artefact",
    "strip_query",
]

PAGE_LIMIT = 100
"""Items asked for in one search, newest first. Thirty days of Sentinel‑2 over
an area is a dozen or two items; ninety of Landsat fewer. A full page is said
out loud, because the oldest candidates were then never seen."""

MAX_SCENE_CLOUD = 80.0
"""Scene-wide cloud above which a granule is not worth reading a window of.
Generous on purpose: this only saves requests. Below it, the area's own cloud
fraction decides."""

_T = TypeVar("_T")
_EPSG_CODE = re.compile(r"^EPSG:([0-9]{4,6})$")


@dataclass(frozen=True, slots=True)
class SceneCandidate:
    """One catalogue item that might show an area.

    ``assets`` maps an asset's name to its address exactly as the catalogue
    gave it; :func:`strip_query` is applied wherever one is written down.
    """

    source_id: str
    item_id: str
    acquired_at: datetime
    acquisition: str
    platform: str
    crs: str
    scene_cloud: float | None
    footprint: Bounds
    assets: Mapping[str, str]
    version: str
    """The item's own version: its ``updated`` time, else its ``created``.
    A window's provenance names this, because a window is read through the
    item that pointed at it."""


@dataclass(frozen=True, slots=True)
class Acquisition:
    """The granules of one pass, which together are one scene."""

    key: str
    acquired_at: datetime
    candidates: tuple[SceneCandidate, ...]

    @property
    def scene_cloud(self) -> float | None:
        """The cloudiest granule's scene-wide figure: the pessimistic order."""
        known = [
            one.scene_cloud for one in self.candidates if one.scene_cloud is not None
        ]
        return max(known) if known else None


def search_artefact(
    source: StacSource, bounds: Bounds, until: datetime
) -> RemoteArtefact:
    """The search for scenes over a box in a source's lookback, newest first.

    A GET with the query in the URL, so the address recorded in the raw store
    is the whole question that was asked.
    """
    if until.tzinfo is None:
        message = "until must carry a time zone"
        raise ValueError(message)
    end = until.astimezone(UTC).replace(microsecond=0)
    start = end - timedelta(days=source.lookback_days)
    box = ",".join(f"{value:.6f}" for value in bounds.to_list())
    interval = f"{_stamp(start)}/{_stamp(end)}"
    query = urlencode(
        {
            "collections": source.collection,
            "bbox": box,
            "datetime": interval,
            "limit": str(PAGE_LIMIT),
            "sortby": "-properties.datetime",
        },
        safe=",/:",
    )
    return RemoteArtefact(
        url=f"{source.search_url}?{query}",
        original_identifier=f"search {source.collection} bbox={box} {interval}",
        payload_kind="data",
        valid_from=start,
        valid_to=end,
        spatial_extent=dict(
            zip(("west", "south", "east", "north"), bounds.to_list(), strict=True)
        ),
    )


def parse_search(
    body: bytes, source: StacSource
) -> tuple[tuple[SceneCandidate, ...], tuple[str, ...], bool]:
    """The candidates a stored search answer offers.

    Returns:
        The candidates; why each other item was set aside; and whether the
        page was full, so older items may exist that were never seen.

    Raises:
        MalformedImageryError: The answer is not a STAC item collection.
    """
    try:
        stored = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        message = f"the {source.collection} search answer is not JSON: {exc}"
        raise MalformedImageryError(message) from exc
    if not isinstance(stored, Mapping) or not isinstance(stored.get("features"), list):
        message = f"the {source.collection} search answer has no features list"
        raise MalformedImageryError(message)
    features: Sequence[object] = stored["features"]
    kept: list[SceneCandidate] = []
    set_aside: list[str] = []
    for feature in features:
        candidate, reason = _candidate(feature, source)
        if candidate is not None:
            kept.append(candidate)
        else:
            set_aside.append(reason)
    return tuple(kept), tuple(set_aside), len(features) >= PAGE_LIMIT


def acquisitions(
    candidates: Sequence[SceneCandidate], bounds: Bounds
) -> tuple[Acquisition, ...]:
    """Candidates over the box grouped by pass and grid, least cloudy and newest first.

    A granule whose footprint misses the box is dropped; one over
    :data:`MAX_SCENE_CLOUD` is dropped. Within a pass, granules are ordered by
    item id, so a mosaic places them the same way every time.
    """
    groups: dict[str, list[SceneCandidate]] = {}
    for one in candidates:
        if not _overlaps(one.footprint, bounds):
            continue
        if one.scene_cloud is not None and one.scene_cloud > MAX_SCENE_CLOUD:
            continue
        groups.setdefault(f"{one.acquisition} {one.crs}", []).append(one)
    passes = [
        Acquisition(
            key=key,
            acquired_at=min(one.acquired_at for one in members),
            candidates=tuple(sorted(members, key=lambda one: one.item_id)),
        )
        for key, members in groups.items()
    ]
    return tuple(
        sorted(
            passes,
            key=lambda one: (
                one.scene_cloud if one.scene_cloud is not None else 101.0,
                -one.acquired_at.timestamp(),
                one.key,
            ),
        )
    )


def strip_query(href: str) -> str:
    """An address without its query or fragment, which is how one is written.

    A provider may append a read token to an asset's address, and its terms
    forbid passing that on (D-371), so no address is recorded with a query.
    """
    parts = urlsplit(href)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


class _SetAsideError(ValueError):
    """An item that cannot be a candidate, and why, as the run will say it."""


def _need(value: _T | None, reason: str) -> _T:
    if value is None:
        raise _SetAsideError(reason)
    return value


def _text(mapping: Mapping[str, object], key: str) -> str | None:
    value = mapping.get(key)
    return value if isinstance(value, str) and value.strip() else None


def _candidate(
    feature: object, source: StacSource
) -> tuple[SceneCandidate | None, str]:
    try:
        return _read_candidate(feature, source), ""
    except _SetAsideError as exc:
        return None, str(exc)


def _read_candidate(feature: object, source: StacSource) -> SceneCandidate:
    if not isinstance(feature, Mapping):
        raise _SetAsideError("an item that is not a JSON object")
    item_id = _need(_text(feature, "id"), "an item with no id")
    properties = feature.get("properties")
    if not isinstance(properties, Mapping):
        raise _SetAsideError(f"{item_id}: no properties")
    platform = _need(_text(properties, "platform"), f"{item_id}: no platform")
    if source.platforms and platform.lower() not in source.platforms:
        raise _SetAsideError(f"{item_id}: {platform} is not one of {source.platforms}")
    acquired = _need(
        _instant(properties.get("datetime")), f"{item_id}: no acquisition time"
    )
    assets = _assets(feature.get("assets"))
    missing = [
        name
        for name in (*source.display_assets, source.classification_asset)
        if name not in assets
    ]
    if missing:
        raise _SetAsideError(f"{item_id}: lacks the {', '.join(missing)} asset")
    cloud = properties.get("eo:cloud_cover")
    return SceneCandidate(
        source_id=source.source_id,
        item_id=item_id,
        acquired_at=acquired,
        acquisition=_acquisition(item_id, platform, acquired, properties),
        platform=platform,
        crs=_need(_crs(properties), f"{item_id}: no EPSG code for its grid"),
        scene_cloud=(
            float(cloud)
            if isinstance(cloud, int | float) and not isinstance(cloud, bool)
            else None
        ),
        footprint=_need(_footprint(feature.get("bbox")), f"{item_id}: no usable bbox"),
        assets=assets,
        version=_need(
            _version(properties),
            f"{item_id}: no updated or created time to version it by",
        ),
    )


def _acquisition(
    item_id: str, platform: str, acquired: datetime, properties: Mapping[str, object]
) -> str:
    """Which pass an item belongs to.

    Sentinel‑2 names its datatake. Landsat's pass is a platform on a day along
    one WRS-2 path; rows down a path are one continuous acquisition. Anything
    else is its own pass.
    """
    datatake = properties.get("s2:datatake_id")
    if isinstance(datatake, str) and datatake.strip():
        return datatake
    path = properties.get("landsat:wrs_path")
    if isinstance(path, str | int) and not isinstance(path, bool):
        return f"{platform.lower()}/{acquired:%Y-%m-%d}/path {path}"
    return item_id


def _crs(properties: Mapping[str, object]) -> str | None:
    code = properties.get("proj:code")
    if isinstance(code, str) and _EPSG_CODE.match(code):
        return code
    epsg = properties.get("proj:epsg")
    if isinstance(epsg, int) and not isinstance(epsg, bool) and epsg > 0:
        return f"EPSG:{epsg}"
    return None


def _assets(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    found: dict[str, str] = {}
    for name, asset in value.items():
        if not isinstance(name, str) or not isinstance(asset, Mapping):
            continue
        href = asset.get("href")
        if isinstance(href, str) and href.startswith("https://"):
            found[name] = href
    return found


def _footprint(value: object) -> Bounds | None:
    if not isinstance(value, list) or len(value) != 4:  # noqa: PLR2004 — w, s, e, n
        return None
    try:
        return Bounds.from_list(value)
    except MalformedImageryError:
        return None


def _version(properties: Mapping[str, object]) -> str | None:
    for name in ("updated", "created"):
        value = properties.get(name)
        if isinstance(value, str) and value.strip():
            return f"{name} {value.strip()}"
    return None


def _instant(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _overlaps(one: Bounds, other: Bounds) -> bool:
    return (
        one.west < other.east
        and other.west < one.east
        and one.south < other.north
        and other.south < one.north
    )


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")
