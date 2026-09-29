"""A regional report as the lines ``meridian regions report`` prints.

Written from the report's own rows by fixed templates — no language model
writes a word of it (D-098). Every number printed is a field of a row in the
published directory, so a reader can find it there, and every image is called
imagery.

Reference: docs/DECISIONS.md D-098, D-229 to D-233.
"""

from __future__ import annotations

from collections.abc import Iterator

from meridian.datasets.weighting import Rate
from meridian.regions.change import Change
from meridian.regions.report import IMAGERY_LABEL, RegionsReport
from meridian.regions.rows import Area

__all__ = ["report_lines"]


def report_lines(report: RegionsReport) -> list[str]:
    """Every line of the printed report, in a fixed order."""
    lines: list[str] = []
    if not report.areas:
        return ["no area of interest is registered in this snapshot"]
    for area in report.areas:
        lines.extend(_area(report, area))
    return lines


def _area(report: RegionsReport, area: Area) -> Iterator[str]:
    state = "active" if area.active else "retired — nothing computed"
    yield f"area {area.area_id}  {area.label}  ({area.area_km2:.1f} km², {state})"
    if not area.active:
        return
    yield from _series(report, area.area_id)
    yield from _changes(report, area.area_id)
    yield from _coverage(report, area.area_id)
    yield from _checks(report, area.area_id)
    count = sum(1 for one in report.imagery if one.area_id == area.area_id)
    yield f"  {count} tiles held — {IMAGERY_LABEL}"


def _series(report: RegionsReport, area_id: int) -> Iterator[str]:
    points = [one for one in report.series if one.area_id == area_id]
    for quantity in sorted({one.quantity for one in points}):
        mine = [one for one in points if one.quantity == quantity]
        valued = [one for one in mine if one.value is not None]
        latest = valued[-1] if valued else None
        tail = (
            f"latest {latest.value:g} {latest.unit} for "
            f"{latest.period_from.date().isoformat()} from "
            f"{', '.join(latest.products)}"
            if latest is not None
            else "no value"
        )
        yield (
            f"  {quantity:<26} {len(mine):>4} points, {len(mine) - len(valued)} "
            f"missing; {tail}"
        )


def _changes(report: RegionsReport, area_id: int) -> Iterator[str]:
    for one in report.changes:
        if one.area_id == area_id:
            yield f"  change {one.quantity:<19} {_change(one)}"


def _change(one: Change) -> str:
    if one.change is None:
        return f"insufficient — {one.reason}"
    unit = "" if one.rule.kind == "absolute" else " (relative)"
    return (
        f"{one.verdict.upper()}: {one.change:+.3f}{unit}, interval "
        f"[{one.low:+.3f}, {one.high:+.3f}] against {one.rule.threshold:+g}; "
        f"n = {one.baseline_n} then {one.current_n}"
    )


def _coverage(report: RegionsReport, area_id: int) -> Iterator[str]:
    mine = [one for one in report.coverage if one.area_id == area_id]
    measured = [one for one in mine if not one.simulated]
    yield f"  covered by {len(measured)} of our measured decoded receptions"
    if report.include_simulated:
        yield f"  covered by {len(mine) - len(measured)} SIMULATED receptions, apart"


def _checks(report: RegionsReport, area_id: int) -> Iterator[str]:
    for one in report.ingest:
        if one.area_id == area_id and one.days:
            gaps = (
                f", missing {', '.join(one.missing_days)}" if one.missing_days else ""
            )
            yield (
                f"  ingest check {one.quantity:<21} {one.present}/{one.days} "
                f"imaged days held{_interval(one.rate)}{gaps}"
            )
    for check in report.weather:
        if check.area_id == area_id:
            yield (
                f"  chain check wet vs dry       {check.verdict}: wet"
                f"{_interval(check.wet)} over {check.wet_attempts}, dry"
                f"{_interval(check.dry)} over {check.dry_attempts}, "
                f"{check.unknown_days} without rain data"
            )


def _interval(rate: Rate | None) -> str:
    if rate is None:
        return " —"
    return f" {rate.estimate:.2f} [{rate.low:.2f}, {rate.high:.2f}]"
