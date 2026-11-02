"""A sealed regional report, summarised for the public read API once D-137 allows it.

``meridian regions summary <report> --output <file>`` writes ``areas-summary-1``:
the report's hash and method, each active area's label, size and bounding
box, every series point of the public products, and every change with its
interval and verdict, copied from the report's own files (D-229). The API
reads the file and nothing else, because no runtime module may import this
package (D-228), and serves it only when the deployment says areas are
published, which D-326 proposes and the team must accept first (D-227).

**Coverage is left out.** It counts our own network's receptions over an area,
which may be simulated, and is published with the reception figures it
belongs to, not here.

**No note and no polygon.** An area's notes never leave the operator (D-227),
and its shape is published as a bounding box, which is what a map needs and
all a reader can check against the series.

Reference: docs/DECISIONS.md D-227, D-228, D-229, D-326.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from meridian.datasets.manifest import content_sha256
from meridian.datasets.publish import SnapshotDirectory
from meridian.regions.series import METHOD_VERSION

__all__ = ["SCHEMA", "NotSummarisableError", "areas_summary", "write_summary"]

SCHEMA = "areas-summary-1"

_SERIES_FIELDS = (
    "area_id",
    "quantity",
    "unit",
    "period_from",
    "period_until",
    "value",
    "missing_reason",
    "count",
    "sources",
)
_CHANGE_FIELDS = (
    "area_id",
    "quantity",
    "verdict",
    "reason",
    "baseline",
    "current",
    "baseline_mean",
    "current_mean",
    "change",
    "low",
    "high",
)

Row = Mapping[str, object]


class NotSummarisableError(ValueError):
    """A directory this summary cannot describe."""


def areas_summary(report: SnapshotDirectory) -> dict[str, object]:
    """The summary of one sealed regional report.

    Raises:
        NotSummarisableError: The directory is not a regional report, or holds
            no active area.
    """
    if report.manifest.kind != "regions_report":
        message = f"{report.path} is a {report.manifest.kind}, not a regional report"
        raise NotSummarisableError(message)
    areas = [one for one in _rows(report, "areas") if one.get("active") is True]
    if not areas:
        message = f"{report.path} holds no active area, so there is nothing to publish"
        raise NotSummarisableError(message)
    shown = {one["area_id"] for one in areas}
    return {
        "schema": SCHEMA,
        "report_sha256": content_sha256(report.manifest).hex(),
        "method": METHOD_VERSION,
        "since": report.manifest.since.isoformat(),
        "as_of": report.manifest.as_of.isoformat(),
        "sources": [
            {
                "source_id": one.source_id,
                "licence": one.licence,
                "terms_url": one.terms_url,
            }
            for one in sorted(report.manifest.sources, key=lambda one: one.source_id)
        ],
        "areas": [
            {
                "area_id": one["area_id"],
                "label": one["label"],
                "area_km2": one["area_km2"],
                "bbox": one["bbox"],
            }
            for one in areas
        ],
        "series": _kept(_rows(report, "series"), shown, _SERIES_FIELDS),
        "changes": _kept(_rows(report, "changes"), shown, _CHANGE_FIELDS),
    }


def write_summary(path: Path, summary: Mapping[str, object]) -> None:
    """Write a summary so a reader never sees half of it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_name(f".{path.name}.partial")
    scratch.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    scratch.replace(path)


def _kept(
    rows: Sequence[Row], shown: set[object], fields: tuple[str, ...]
) -> list[dict[str, object]]:
    """The rows of active areas, each with only the fields published."""
    return [
        {name: row.get(name) for name in fields}
        for row in rows
        if row.get("area_id") in shown
    ]


def _rows(report: SnapshotDirectory, name: str) -> list[Row]:
    data = report.files.get(f"{name}.jsonl", b"")
    return [json.loads(line) for line in data.decode("utf-8").splitlines() if line]
