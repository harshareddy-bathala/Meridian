"""``meridian regions summary``: a report's areas, series and changes, nothing more.

What the public read API may serve once a deployment publishes it (D-326): active areas
only, a label and a bounding box but never a note or a polygon, and no coverage,
which counts our own receptions and may be simulated.

Reference: docs/DECISIONS.md D-227, D-228, D-229, D-326.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from meridian.datasets.canonical import canonical_line
from meridian.datasets.manifest import Manifest, content_sha256, file_entry
from meridian.datasets.publish import SnapshotDirectory
from meridian.regions.summary import SCHEMA, NotSummarisableError, areas_summary

SINCE = datetime(2026, 10, 5, tzinfo=UTC)
AS_OF = datetime(2026, 11, 2, tzinfo=UTC)
PERIOD = {"from": "2026-10-05T00:00:00+00:00", "until": "2026-10-21T00:00:00+00:00"}

TABLES = {
    "areas": [
        {
            "area_id": 1,
            "label": "Bengaluru urban",
            "area_km2": 741.0,
            "active": True,
            "bbox": [77.46, 12.83, 77.75, 13.14],
            "geometry_sha256": "ab",
        },
        {
            "area_id": 2,
            "label": "Retired",
            "area_km2": 10.0,
            "active": False,
            "bbox": [0.0, 0.0, 0.1, 0.1],
            "geometry_sha256": "cd",
        },
    ],
    "series": [
        {
            "area_id": 1,
            "quantity": "ndvi",
            "unit": "1",
            "period_from": PERIOD["from"],
            "period_until": PERIOD["until"],
            "value": 0.41,
            "missing_reason": None,
            "count": 3,
            "method": "mean",
            "sources": ["ornl_modis_ndvi"],
            "products": ["MOD13Q1"],
            "record_ids": [7, 8, 9],
        },
        {
            "area_id": 2,
            "quantity": "ndvi",
            "unit": "1",
            "period_from": PERIOD["from"],
            "period_until": PERIOD["until"],
            "value": 0.2,
            "missing_reason": None,
            "count": 1,
            "method": "mean",
            "sources": ["ornl_modis_ndvi"],
            "products": ["MOD13Q1"],
            "record_ids": [10],
        },
    ],
    "changes": [],
    "coverage": [{"area_id": 1, "decoded": 4}],
}


def report(
    tables: dict[str, list[dict[str, object]]], kind: str = "regions_report"
) -> SnapshotDirectory:
    files = {
        f"{name}.jsonl": b"".join(canonical_line(row) for row in rows)
        for name, rows in tables.items()
    }
    manifest = Manifest(
        kind=kind,  # type: ignore[arg-type]
        schema_revision="0032",
        since=SINCE,
        as_of=AS_OF,
        files=tuple(file_entry(name, data) for name, data in sorted(files.items())),
        created_at=AS_OF,
        **(
            {}
            if kind == "raw_snapshot"
            else {
                "derived_from": b"\x01" * 32,
                "transformation_version": "regions-1",
                "config_sha256": b"\x02" * 32,
            }
        ),
    )
    return SnapshotDirectory(path=Path("regions/x"), manifest=manifest, files=files)


def test_active_areas_their_series_and_nothing_private() -> None:
    sealed = report(TABLES)
    summary = areas_summary(sealed)

    assert summary["schema"] == SCHEMA
    assert summary["report_sha256"] == content_sha256(sealed.manifest).hex()
    assert summary["areas"] == [
        {
            "area_id": 1,
            "label": "Bengaluru urban",
            "area_km2": 741.0,
            "bbox": [77.46, 12.83, 77.75, 13.14],
        }
    ]
    (point,) = summary["series"]  # type: ignore[misc]
    assert point["area_id"] == 1
    assert "record_ids" not in point
    assert "coverage" not in summary, "our receptions are not an area's figure"


def test_a_report_with_no_active_area_is_refused() -> None:
    retired = {**TABLES, "areas": [TABLES["areas"][1]]}
    with pytest.raises(NotSummarisableError, match="no active area"):
        areas_summary(report(retired))


def test_another_kind_of_directory_is_refused() -> None:
    with pytest.raises(NotSummarisableError, match="not a regional report"):
        areas_summary(report(TABLES, kind="raw_snapshot"))
