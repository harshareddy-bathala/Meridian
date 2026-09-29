"""``meridian regions`` — areas of interest, their reports, and their alerts.

Five verbs:

* ``add --label … (--bbox W,S,E,N | --geojson PATH) [--notes …]`` registers an
  area. An operator's act, as admitting a station is: there is no endpoint that
  creates one (D-137, D-227);
* ``list`` prints every area, active and retired;
* ``retire <area id>`` stops computing for an area without deleting it;
* ``report --snapshot DIR [--config FILE] [--root DIR]`` computes a regional
  report from a raw snapshot alone — no database, no network — publishes it,
  and prints it. Run twice, it names the same directory (D-229);
* ``record-alerts --report DIR`` records a report's alerts and hands each to
  the delivery interface, which records only until Stage 29 (D-232).

Reference: docs/DECISIONS.md D-227 to D-233.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from meridian.config import load_settings
from meridian.datasets.publish import DamagedSnapshotError
from meridian.regions.alerts import RecordOnlyDelivery, read_alerts, record_alerts
from meridian.regions.areas import AreaRefusedError, NewArea, new_area
from meridian.regions.config import (
    RegionsConfig,
    RegionsConfigError,
    load_regions_config,
)
from meridian.regions.geometry import GeometryError, Polygon
from meridian.regions.publish import publish_report
from meridian.regions.report_text import report_lines
from meridian.store.areas_of_interest import (
    NewAreaRow,
    insert_area,
    list_areas,
    retire_area,
)
from meridian.store.pool import DatabaseUnreachableError, connect_once
from meridian.store.stations import Connection

__all__ = ["add_regions_parser", "run_regions"]

EXIT_FAILED = 1
DEFAULT_ROOT = Path("data/datasets")


def add_regions_parser(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Wire ``meridian regions`` and its five actions."""
    regions = subcommands.add_parser(
        "regions",
        help="areas of interest: register them, report on them, record alerts",
        description=(
            "An area of interest is a place and a label, never a person "
            "(D-227). Reports are computed from a snapshot alone (D-229)."
        ),
    )
    actions = regions.add_subparsers(dest="action", metavar="<action>")
    add = actions.add_parser("add", help="register an area of interest")
    add.add_argument("--label", required=True)
    shape = add.add_mutually_exclusive_group(required=True)
    shape.add_argument("--bbox", help="west,south,east,north in degrees")
    shape.add_argument("--geojson", type=Path, help="a file holding one Polygon")
    add.add_argument("--notes", default=None)
    actions.add_parser("list", help="every area, active and retired")
    retire = actions.add_parser("retire", help="stop watching an area")
    retire.add_argument("area_id", type=int)
    report = actions.add_parser("report", help="compute a report from a snapshot")
    report.add_argument("--snapshot", type=Path, required=True)
    report.add_argument("--config", type=Path, default=None)
    report.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    record = actions.add_parser("record-alerts", help="record a report's alerts")
    record.add_argument("--report", type=Path, required=True)


def run_regions(args: argparse.Namespace) -> int:
    """Run one ``meridian regions`` action."""
    if args.action == "report":
        return _report(args)
    actions = {
        "add": _add,
        "list": _list,
        "retire": _retire,
        "record-alerts": _record,
    }
    try:
        area = (
            new_area(args.label, _polygon(args), args.notes)
            if args.action == "add"
            else None
        )
        with connect_once(load_settings()) as conn:
            return actions[args.action](conn, args, area)
    except (AreaRefusedError, GeometryError, ValueError, DamagedSnapshotError) as exc:
        return _refuse(args.action, str(exc))
    except (DatabaseUnreachableError, psycopg.Error) as exc:
        return _refuse(args.action, f"the database did not answer: {exc}")


def _polygon(args: argparse.Namespace) -> Polygon:
    if args.bbox is not None:
        parts = [float(one) for one in str(args.bbox).split(",")]
        if len(parts) != 4:  # noqa: PLR2004 — a box has four sides
            message = "--bbox is west,south,east,north"
            raise GeometryError(message)
        return Polygon.from_bbox(*parts)
    return Polygon.from_geojson(json.loads(Path(args.geojson).read_text("utf-8")))


def _add(conn: Connection, _args: argparse.Namespace, area: NewArea | None) -> int:
    if area is None:  # pragma: no cover — run_regions builds it for "add"
        return _refuse("add", "no area was described")
    lat, lon = area.centroid
    arrival = insert_area(
        conn,
        NewAreaRow(
            label=area.label,
            geometry=area.polygon.to_geojson(),
            geometry_sha256=area.polygon.sha256,
            centroid_lat_deg=lat,
            centroid_lon_deg=lon,
            area_km2=area.area_km2,
            notes=area.notes,
        ),
    )
    held = (
        "registered"
        if arrival.written
        else "already registered with this shape"
        if arrival.active
        else "already registered with this shape, and retired"
    )
    _say(f"area {arrival.area_id} {held}: {area.label} ({area.area_km2:.1f} km²)")
    return 0


def _list(conn: Connection, _args: argparse.Namespace, _area: NewArea | None) -> int:
    for one in list_areas(conn):
        state = "active" if one.active else "retired"
        _say(
            f"{one.area_id:>4}  {state:<7}  {one.area_km2:>10.1f} km²  "
            f"({one.centroid_lat_deg:.3f}, {one.centroid_lon_deg:.3f})  {one.label}"
        )
    return 0


def _retire(conn: Connection, args: argparse.Namespace, _area: NewArea | None) -> int:
    if not retire_area(conn, args.area_id):
        return _refuse("retire", f"no active area {args.area_id}")
    _say(f"area {args.area_id} retired; its earlier reports are unchanged")
    return 0


def _record(conn: Connection, args: argparse.Namespace, _area: NewArea | None) -> int:
    alerts = read_alerts(args.report)
    recorded = record_alerts(conn, alerts, RecordOnlyDelivery())
    _say(
        f"{recorded.written} of {recorded.alerts} alerts recorded; "
        f"{recorded.alerts - recorded.written} were already held; "
        f"{recorded.delivered} handed to delivery, which is record-only until "
        "Stage 29."
    )
    return 0


def _report(args: argparse.Namespace) -> int:
    try:
        config = (
            load_regions_config(args.config)
            if args.config is not None
            else RegionsConfig()
        )
        published, report = publish_report(
            args.snapshot, config, root=args.root, created_at=datetime.now(UTC)
        )
    except (RegionsConfigError, DamagedSnapshotError, ValueError) as exc:
        return _refuse("report", str(exc))
    held = "written" if published.written else "already held, identically"
    _say(f"{published.path}  {held}")
    for line in report_lines(report):
        _say(line)
    return 0


def _say(line: str) -> None:
    print(line)  # noqa: T201 — this is a CLI; stdout is the interface


def _refuse(action: str, reason: str) -> int:
    print(  # noqa: T201 — this is a CLI; stderr is the interface
        f"meridian regions {action}: {reason}", file=sys.stderr
    )
    return EXIT_FAILED
