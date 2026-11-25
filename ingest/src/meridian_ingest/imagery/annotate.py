"""Every marker on a scene, placed from records and citing them, in one order.

A scene's markers are, in this order, and numbered from one in it:

1. **the area**, drawn as the box ``areas-summary-1`` publishes, never its
   polygon (D-326), citing the summary;
2. **its changes**, each a statement the regional report made about the box:
   what changed, between which periods, by how much and with what verdict;
3. **fires**, FIRMS detections inside the box within a day of the scene
   (:mod:`.annotate_fires`);
4. **places**, from the gazetteer the area's size picks (:mod:`.gazetteer`).

**Annotation never sees a raster.** It takes records and a box and returns
records; nothing here can read the picture it will be drawn on, so a marker
cannot be placed by what the scene looks like (D-133). The boundary test holds
it to no image library and no NumPy.

**Meridian's own records are cited where they are served.** The area and its
changes come from the published summary, and the API serves that summary at
``/api/v1/areas`` on the same host as the image, so that is the address each
cites. The record id names the sealed report by its hash.

Reference: docs/DECISIONS.md D-133, D-229, D-326, D-374.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from meridian_ingest.imagery.annotate_fires import FireRecords, fire_records
from meridian_ingest.imagery.areas import PublishedArea, PublishedAreas
from meridian_ingest.imagery.gazetteer import PlaceLabels, place_labels
from meridian_ingest.imagery.model import (
    Annotation,
    Citation,
    Input,
    MalformedImageryError,
)
from meridian_ingest.raw_store import RawStore

__all__ = [
    "AREAS_LICENCE",
    "AREAS_SOURCE",
    "AREAS_URL",
    "AreaChange",
    "SceneMarkers",
    "annotate_scene",
    "marker_key",
    "read_changes",
]

AREAS_SOURCE = "meridian_areas"
AREAS_URL = "/api/v1/areas"
AREAS_LICENCE = (
    "Meridian's published areas summary; each value keeps its source's terms"
)


@dataclass(frozen=True, slots=True)
class AreaChange:
    """One change the summary states for an area, as the marker says it."""

    quantity: str
    verdict: str
    reason: str
    baseline: tuple[str, str]
    current: tuple[str, str]
    change: float | None
    low: float | None
    high: float | None

    def text(self) -> str:
        """The change in words: what, how much, between when, and its verdict."""
        name = self.quantity.replace("_", " ")
        periods = f"{_period(self.current)} against {_period(self.baseline)}"
        if self.change is None or self.low is None or self.high is None:
            return f"Change in {name}, {periods}: {self.verdict}, {self.reason}"
        return (
            f"Change in {name}, {periods}: {self.verdict}, {self.change:+.3g} "
            f"(interval {self.low:+.3g} to {self.high:+.3g}); {self.reason}"
        )


@dataclass(frozen=True, slots=True)
class SceneMarkers:
    """A scene's markers in drawing order, and every record they were read from."""

    annotations: tuple[Annotation, ...]
    inputs: tuple[Input, ...]
    fires: FireRecords
    places: PlaceLabels
    changes: int

    @property
    def credits(self) -> tuple[str, ...]:
        """The credit each marked source asks for, beside the image's own.

        GeoNames' licence asks for it (CC BY 4.0); FIRMS asks for a citation;
        Natural Earth asks for nothing and is credited anyway, as
        ``ATTRIBUTION.md`` says.
        """
        credits = []
        if self.fires.annotations:
            credits.append("Fire detections: NASA FIRMS.")
        if self.places.places:
            credits.append(
                "Place names © GeoNames, CC BY 4.0."
                if self.places.named_by == "GeoNames"
                else "Place names made with Natural Earth."
            )
        return tuple(credits)


def annotate_scene(  # noqa: PLR0913 — the area, its summary and the records' store
    store: RawStore,
    summary_path: Path,
    published: PublishedAreas,
    area: PublishedArea,
    *,
    acquired_at: datetime,
    gazetteer: str | None = None,
) -> SceneMarkers:
    """Every marker on a scene of ``area`` acquired at ``acquired_at``.

    Args:
        store: The raw store FIRMS and the gazetteers were fetched into.
        summary_path: The ``areas-summary-1`` file ``published`` was read from.
        published: The published areas, and the summary's digests.
        area: The area the scene shows.
        acquired_at: When the scene was acquired.
        gazetteer: The area's chosen gazetteer, overriding the size rule.

    Raises:
        MalformedImageryError: The summary's changes cannot be read.
    """
    changes = read_changes(summary_path, area.area_id)
    fires = fire_records(store, area.bounds, acquired_at)
    places = place_labels(store, area.bounds, gazetteer)
    own = (
        _area_marker(published.report_sha256, area),
        *(_change_marker(published.report_sha256, area, one) for one in changes),
    )
    return SceneMarkers(
        annotations=(*own, *fires.annotations, *places.annotations()),
        inputs=(
            Input("areas_summary", published.report_sha256, published.file_sha256),
            *fires.inputs,
            *places.inputs(),
        ),
        fires=fires,
        places=places,
        changes=len(changes),
    )


_KEY_WORDS = {
    "area": ("the area's box", "the area's box"),
    "change": ("a change over it", "changes over it"),
    "fire": ("a fire detection", "fire detections"),
}


def marker_key(markers: Sequence[Annotation]) -> str:
    """The line under the picture that says what its numbers are.

    Runs of one kind are written as a range — ``4–9 fire detections`` — in
    the order they are numbered. Places are named where they lie, so the key
    says so instead of numbering them.
    """
    runs: list[tuple[str, int, int]] = []
    for number, marker in enumerate(markers, start=1):
        if runs and runs[-1][0] == marker.kind:
            runs[-1] = (marker.kind, runs[-1][1], number)
        else:
            runs.append((marker.kind, number, number))
    parts = []
    for kind, first, last in runs:
        if kind == "place":
            parts.append("places are named where they lie")
            continue
        one, many = _KEY_WORDS[kind]
        if first == last:
            parts.append(f"{first} {one}")
        else:
            parts.append(f"{first}–{last} {many}")
    return "Markers: " + "; ".join(parts) + "." if parts else ""


def _citation(report_sha256: str, record: str) -> Citation:
    return Citation(
        AREAS_SOURCE,
        f"areas-summary-1 of report {report_sha256[:12]}, {record}",
        AREAS_URL,
        AREAS_LICENCE,
    )


def _area_marker(report_sha256: str, area: PublishedArea) -> Annotation:
    return Annotation(
        kind="area",
        geometry=area.bounds,
        label=area.label[:64],
        text=(
            f"{area.label}: the published area, drawn as its bounding box; its "
            f"outline encloses {area.area_km2:,.0f} km²"
        ),
        citations=(_citation(report_sha256, f"area {area.area_id}"),),
    )


def _change_marker(
    report_sha256: str, area: PublishedArea, change: AreaChange
) -> Annotation:
    record = f"area {area.area_id}, change in {change.quantity}"
    return Annotation(
        kind="change",
        geometry=area.bounds,
        label=f"Change in {change.quantity.replace('_', ' ')}"[:64],
        text=change.text()[:500],
        citations=(_citation(report_sha256, record),),
    )


def read_changes(path: Path, area_id: int) -> tuple[AreaChange, ...]:
    """The changes an ``areas-summary-1`` file states for one area, in its order.

    Raises:
        MalformedImageryError: The file is unreadable, or a change of this area
            is missing a field it is read by.
    """
    try:
        stored = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        message = f"{path} could not be read for its changes: {exc}"
        raise MalformedImageryError(message) from exc
    entries = stored.get("changes") if isinstance(stored, Mapping) else None
    if not isinstance(entries, list):
        message = f"{path}: changes must be a JSON array"
        raise MalformedImageryError(message)
    return tuple(
        _change(entry, path)
        for entry in entries
        if isinstance(entry, Mapping) and entry.get("area_id") == area_id
    )


def _change(entry: Mapping[str, object], path: Path) -> AreaChange:
    where = f"{path}: a change of area {entry.get('area_id')}"
    quantity, verdict = entry.get("quantity"), entry.get("verdict")
    reason = entry.get("reason")
    if not all(isinstance(one, str) and one for one in (quantity, verdict)):
        raise MalformedImageryError(f"{where} has no quantity or verdict")
    return AreaChange(
        quantity=str(quantity),
        verdict=str(verdict),
        reason=reason if isinstance(reason, str) and reason else "no reason given",
        baseline=_span(entry.get("baseline"), where),
        current=_span(entry.get("current"), where),
        change=_number(entry.get("change"), where),
        low=_number(entry.get("low"), where),
        high=_number(entry.get("high"), where),
    )


def _span(value: object, where: str) -> tuple[str, str]:
    if not isinstance(value, Mapping):
        raise MalformedImageryError(f"{where} has no period")
    start, until = value.get("from"), value.get("until")
    if not isinstance(start, str) or not isinstance(until, str):
        raise MalformedImageryError(f"{where} has a period without its ends")
    return start, until


def _number(value: object, where: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise MalformedImageryError(f"{where} holds {value!r} where a number goes")
    return float(value)


def _period(span: tuple[str, str]) -> str:
    """A period as its two dates, ``2026-10-21 to 2026-11-02``."""
    return f"{span[0][:10]} to {span[1][:10]}"
