"""The areas imagery may show: those in the published ``areas-summary-1`` file.

An area is imaged only once it is published, and published means in the file
``meridian regions summary`` wrote and the deployment named (D-326). So the
pipeline reads its areas from that file and never from ``areas_of_interest``:
an area registered but not published has no image, because nothing here can
see it (D-372).

Only what imaging needs is read — each area's id, label and box — plus the
report's hash, which every scene cites. The rest of the file (series, changes,
sources) belongs to the annotator and the API. An area entry carrying a field
this version does not know is refused, and a polygon especially: a shape
beyond the box is exactly what D-326 keeps out of every published file.

Stdlib only, like the model: reading a file of records needs no image library.

Reference: docs/DECISIONS.md D-227, D-326, D-372.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from meridian_ingest.imagery.model import Bounds, MalformedImageryError

__all__ = [
    "SUMMARY_SCHEMA",
    "PublishedArea",
    "PublishedAreas",
    "read_published_areas",
]

SUMMARY_SCHEMA = "areas-summary-1"
_AREA_FIELDS = frozenset({"area_id", "label", "area_km2", "bbox"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class PublishedArea:
    """One published area, as the summary names it."""

    area_id: int
    label: str
    area_km2: float
    bounds: Bounds


@dataclass(frozen=True, slots=True)
class PublishedAreas:
    """Every published area, and the two digests a scene cites them by.

    ``report_sha256`` names the sealed regional report the summary was made
    from; ``file_sha256`` is the summary file's own bytes, so a scene's inputs
    say exactly which file was read.
    """

    report_sha256: str
    file_sha256: str
    areas: tuple[PublishedArea, ...]

    def chosen(self, area_id: int | None) -> tuple[PublishedArea, ...]:
        """Every area, or the one named.

        Raises:
            LookupError: The named area is not published.
        """
        if area_id is None:
            return self.areas
        for area in self.areas:
            if area.area_id == area_id:
                return (area,)
        published = ", ".join(str(one.area_id) for one in self.areas) or "none"
        message = (
            f"area {area_id} is not in the published summary, so it has no image "
            f"(D-326); published: {published}"
        )
        raise LookupError(message)


def read_published_areas(path: Path) -> PublishedAreas:
    """Read the areas out of an ``areas-summary-1`` file.

    Raises:
        MalformedImageryError: The file is unreadable, not this schema, or an
            area in it breaks a rule.
    """
    try:
        raw = path.read_bytes()
        stored = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        message = f"{path} could not be read as an areas summary: {exc}"
        raise MalformedImageryError(message) from exc
    if not isinstance(stored, Mapping) or stored.get("schema") != SUMMARY_SCHEMA:
        message = f"{path} is not an {SUMMARY_SCHEMA} file"
        raise MalformedImageryError(message)
    report = stored.get("report_sha256")
    if not isinstance(report, str) or not _SHA256.match(report):
        message = f"{path}: report_sha256 must be 64 lower-case hex digits"
        raise MalformedImageryError(message)
    entries = stored.get("areas")
    if not isinstance(entries, list):
        message = f"{path}: areas must be a JSON array"
        raise MalformedImageryError(message)
    areas = tuple(_area(entry, path) for entry in entries)
    ids = [one.area_id for one in areas]
    if len(set(ids)) != len(ids):
        message = f"{path}: an area is listed more than once"
        raise MalformedImageryError(message)
    return PublishedAreas(
        report_sha256=report,
        file_sha256=hashlib.sha256(raw).hexdigest(),
        areas=tuple(sorted(areas, key=lambda one: one.area_id)),
    )


def _area(entry: object, path: Path) -> PublishedArea:
    if not isinstance(entry, Mapping):
        message = f"{path}: an area must be a JSON object"
        raise MalformedImageryError(message)
    unknown = sorted(set(entry) - _AREA_FIELDS)
    if unknown:
        message = (
            f"{path}: an area carries {unknown}, which this version does not read; "
            "a published area is a label and a box (D-326)"
        )
        raise MalformedImageryError(message)
    missing = sorted(_AREA_FIELDS - set(entry))
    if missing:
        message = f"{path}: an area is missing {missing}"
        raise MalformedImageryError(message)
    area_id, label, size = entry["area_id"], entry["label"], entry["area_km2"]
    if isinstance(area_id, bool) or not isinstance(area_id, int) or area_id < 1:
        message = f"{path}: area_id must be a positive integer, not {area_id!r}"
        raise MalformedImageryError(message)
    if not isinstance(label, str) or not label.strip():
        message = f"{path}: area {area_id} has no label"
        raise MalformedImageryError(message)
    if isinstance(size, bool) or not isinstance(size, int | float) or size <= 0:
        message = f"{path}: area {area_id}'s area_km2 must be a positive number"
        raise MalformedImageryError(message)
    return PublishedArea(
        area_id=area_id,
        label=label,
        area_km2=float(size),
        bounds=Bounds.from_list(entry["bbox"], f"area {area_id} bbox"),
    )
