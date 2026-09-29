"""Areas of interest: registered, listed and retired — never deleted.

Reads and writes ``areas_of_interest`` (``deploy/migrations/sql/0022_regions.sql``).
A row is a place and a label; the series and coverage computed about it are
files made from a snapshot, not rows here (D-229).

Reference: docs/DATA-MODEL.md; docs/DECISIONS.md D-137, D-227, D-229.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row
from psycopg.types.json import Jsonb

from meridian.store.stations import Connection

__all__ = [
    "AreaArrival",
    "NewAreaRow",
    "StoredArea",
    "insert_area",
    "list_areas",
    "retire_area",
]


@dataclass(frozen=True, slots=True)
class NewAreaRow:
    """One area in insertable form, derived columns computed by the caller."""

    label: str
    geometry: dict[str, object]
    geometry_sha256: bytes
    centroid_lat_deg: float
    centroid_lon_deg: float
    area_km2: float
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class StoredArea:
    """One row as read back. ``geometry`` is the GeoJSON text, as stored."""

    area_id: int
    label: str
    geometry: str
    centroid_lat_deg: float
    centroid_lon_deg: float
    area_km2: float
    created_at: datetime
    active: bool
    notes: str | None


@dataclass(frozen=True, slots=True)
class AreaArrival:
    """The id, and whether this call wrote it or the shape was already held."""

    area_id: int
    written: bool


@dataclass(frozen=True, slots=True)
class _AreaId:
    area_id: int


def insert_area(conn: Connection, area: NewAreaRow) -> AreaArrival:
    """Register an area, or return the one already holding this exact shape.

    Args:
        conn: An open connection.
        area: The area.

    Returns:
        Its id, and whether this call wrote it.
    """
    with conn.transaction(), conn.cursor(row_factory=class_row(_AreaId)) as cur:
        cur.execute(
            "insert into areas_of_interest (label, geometry, geometry_sha256,"
            " centroid_lat_deg, centroid_lon_deg, area_km2, notes)"
            " values (%s, %s, %s, %s, %s, %s, %s)"
            " on conflict on constraint area_geometry_is_unique do nothing"
            " returning area_id",
            (
                area.label,
                Jsonb(area.geometry),
                area.geometry_sha256,
                area.centroid_lat_deg,
                area.centroid_lon_deg,
                area.area_km2,
                area.notes,
            ),
        )
        inserted = cur.fetchone()
        if inserted is not None:
            return AreaArrival(inserted.area_id, written=True)
        cur.execute(
            "select area_id from areas_of_interest where geometry_sha256 = %s",
            (area.geometry_sha256,),
        )
        held = cur.fetchone()
    if held is None:  # pragma: no cover — the conflict proves the row
        message = "insert conflicted but the conflicting area is not readable"
        raise RuntimeError(message)
    return AreaArrival(held.area_id, written=False)


def list_areas(conn: Connection) -> list[StoredArea]:
    """Every area, active and retired, in id order."""
    with conn.cursor(row_factory=class_row(StoredArea)) as cur:
        cur.execute(
            "select area_id, label, geometry::text as geometry, centroid_lat_deg,"
            " centroid_lon_deg, area_km2, created_at, active, notes"
            " from areas_of_interest order by area_id"
        )
        return cur.fetchall()


def retire_area(conn: Connection, area_id: int) -> bool:
    """Stop watching an area without deleting it. Returns whether one changed."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            "update areas_of_interest set active = false where area_id = %s and active",
            (area_id,),
        )
        return cur.rowcount == 1


def geometry_of(stored: StoredArea) -> dict[str, object]:
    """A stored area's GeoJSON, parsed."""
    parsed = json.loads(stored.geometry)
    if not isinstance(parsed, dict):  # pragma: no cover — the CHECK holds it
        message = f"area {stored.area_id} holds no GeoJSON object"
        raise TypeError(message)
    return {str(key): value for key, value in parsed.items()}
