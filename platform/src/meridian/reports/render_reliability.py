"""The reliability section of ``report.md``, and its figures, from reliability.jsonl.

Reference: docs/DECISIONS.md D-184, D-185, D-192, D-240.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from meridian.reports.markdown import cell, table
from meridian.reports.render_long_run import long_run_lines
from meridian.reports.svg_intervals import Estimate, interval_chart

__all__ = [
    "DETECTION_FIGURE",
    "HISTORY_FIGURE",
    "reliability_figures",
    "render_reliability",
]

HISTORY_FIGURE = "capture_history.svg"
DETECTION_FIGURE = "fault_detection.svg"

Row = Mapping[str, object]


def reliability_figures(rows: Sequence[Row]) -> dict[str, bytes]:
    """Capture over each history window, and detection by fault kind."""
    figures: dict[str, bytes] = {}
    population, history = _history_rows(rows)
    if history:
        sc4 = _of(rows, "sc4")[0]
        figures[HISTORY_FIGURE] = interval_chart(
            (
                f"Capture rate, {population} passes, window by window",
                f"{sc4['window_days']}-day windows, Wilson 95% intervals",
            ),
            [
                Estimate(
                    f"to {str(one['end'])[:10]}"
                    + (" (partial)" if one["partial"] else ""),
                    float(str(_table(one["capture"])["estimate"])),
                    *_bounds(_table(one["capture"])["interval"]),
                )
                for one in history
            ],
            unit="passes captured over eligible passes",
            target=(float(str(sc4["target"])), "SC-4 target"),
            simulated=population == "simulated",
        )
    detection = [
        one
        for one in _of(rows, "latency")
        if one["check"] == "detected" and one["kind"] != "all" and one["n"]
    ]
    if detection:
        figures[DETECTION_FIGURE] = interval_chart(
            (
                "Seconds from a fault to its station reading offline",
                "median per fault kind, bars from the fastest to the slowest",
            ),
            [
                Estimate(
                    str(one["kind"]),
                    float(str(one["median_s"])),
                    float(str(one["min_s"])),
                    float(str(one["max_s"])),
                )
                for one in detection
            ],
            unit="seconds",
            target=(float(str(detection[0]["threshold_s"])), "SC-5"),
            simulated=True,
        )
    return figures


def render_reliability(rows: Sequence[Row]) -> list[str]:
    """The section's lines, without newlines."""
    window = _of(rows, "window")[0]
    absent = _table(window["not_in_a_snapshot"])
    return [
        "## Reliability",
        "",
        f"Counted from the labels over the {window['days']} days to"
        f" {cell(window['end'])}, as `meridian snapshot reliability` counts them."
        " A pass is a miss only if its station was confirmed listening (rule 7)."
        f" Not in a snapshot: station availability — {absent['availability']};"
        f" submission delay — {absent['submission_delay']}.",
        "",
        *_sc4(_of(rows, "sc4")[0]),
        *_indicators(rows),
        *_targets(rows),
        *_history(rows),
        *_faults(rows),
    ]


def _of(rows: Sequence[Row], kind: str) -> list[Row]:
    return [one for one in rows if one["row"] == kind]


def _table(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _bounds(value: object) -> tuple[float | None, float | None]:
    held = _table(value)
    if not held:
        return None, None
    return float(str(held["low"])), float(str(held["high"]))


def _span(value: object) -> str:
    held = _table(value)
    return f"[{held['low']:.3f}, {held['high']:.3f}]" if held else "—"


def _rate(value: object) -> str:
    held = _table(value)
    if held.get("estimate") is None:
        return "— (none counted)"
    return (
        f"{cell(held['estimate'])} {_span(held['interval'])},"
        f" {held['numerator']} of {held['denominator']}"
    )


def _sc4(sc4: Row) -> list[str]:
    if sc4["status"] != "measured":
        return ["### SC-4", "", f"Not measured: {sc4['reason']}.", ""]
    window = (
        ""
        if sc4["sc4_window"]
        else f" This window is {sc4['window_days']} days; SC-4 is a 30-day figure."
    )
    return [
        "### SC-4",
        "",
        f"Measured capture over the window is {cell(sc4['capture'])}, 95% interval"
        f" {_span(sc4['interval'])}, over {sc4['n']} passes. SC-4's target is"
        f" {cell(sc4['target'])}. The point estimate meets it:"
        f" {cell(sc4['point_meets'])}. The whole interval is above it:"
        f" {cell(sc4['interval_above'])}.{window}",
        "",
    ]


def _indicators(rows: Sequence[Row]) -> list[str]:
    body = []
    for one in _of(rows, "indicators"):
        budget = _table(one["budget"])
        body.append(
            (
                cell(one["population"]),
                cell(one["passes"]),
                _rate(one["capture"]),
                _rate(one["confirmed_miss"]),
                _rate(one["completion"]),
                _rate(one["execution"]),
                f"{budget['spent']} of {cell(budget['allowed'])} spent",
            )
        )
    headers = (
        "Population",
        "Passes",
        "Capture",
        "Confirmed miss",
        "Completion",
        "Execution",
        "Loss budget",
    )
    return ["### Indicators", "", *table(headers, body), ""]


def _targets(rows: Sequence[Row]) -> list[str]:
    body = [
        (
            cell(one["population"]),
            cell(one["name"]) + (f" ({one['claim']})" if one["claim"] else ""),
            ("≥ " if one["at_least"] else "≤ ") + cell(one["target"]),
            cell(one["value"]),
            cell(one["met"]),
        )
        for one in _of(rows, "target")
    ]
    headers = ("Population", "Figure", "Target", "Value", "Met")
    return [
        "**Targets** — SC-4 and SC-5 are the project's claims; the rest are"
        " proposed, to agree with the team",
        "",
        *table(headers, body),
        "",
    ]


def _history_rows(rows: Sequence[Row]) -> tuple[str, list[Row]]:
    """Measured history where it has a figure; simulated only when it does not."""
    for population in ("measured", "simulated"):
        found = [
            one
            for one in _of(rows, "history")
            if one["population"] == population
            and _table(one["capture"]).get("estimate") is not None
        ]
        if found:
            return population, found
    return "measured", []


def _history(rows: Sequence[Row]) -> list[str]:
    body = []
    for one in _of(rows, "history"):
        if not _table(one["capture"]).get("denominator"):
            continue
        budget = _table(one["budget"])
        body.append(
            (
                cell(one["population"]),
                str(one["end"])[:10] + (" (partial)" if one["partial"] else ""),
                _rate(one["capture"]),
                f"{budget['spent']} of {cell(budget['allowed'])}",
                cell(budget["remaining_ratio"]),
            )
        )
    headers = ("Population", "Window to", "Capture", "Budget spent", "Budget left")
    figure = (
        [f"![Capture rate, window by window]({HISTORY_FIGURE})", ""]
        if _history_rows(rows)[1]
        else []
    )
    return [
        "### Loss-budget history",
        "",
        "The same window ending at the snapshot and every step before it. A"
        " window reaching before the snapshot began is partial.",
        "",
        *table(headers, body),
        "",
        *figure,
    ]


def _faults(rows: Sequence[Row]) -> list[str]:
    runs = _of(rows, "fault_run")
    lines = ["### Fault runs — SIMULATED", ""]
    if not runs:
        lines.extend(["No fault run was given, so nothing below is measured.", ""])
    else:
        lines.extend(_runs(runs))
        lines.extend(_latencies(rows))
    lines.extend(
        [*_sc5(_of(rows, "sc5")[0]), *long_run_lines(_of(rows, "long_run")[0])]
    )
    if any(one["check"] == "detected" and one["n"] for one in _of(rows, "latency")):
        lines.extend([f"![Detection by fault kind]({DETECTION_FIGURE})", ""])
    return lines


def _runs(runs: Sequence[Row]) -> list[str]:
    body = [
        (
            f"`{str(one['sha256'])[:12]}`",
            f"{cell(one['since'])} to {cell(one['as_of'])}",
            cell(one["hours"]),
            f"{one['faults']} ({one['failed']} failed)",
            cell(one["agrees_with_published"]),
        )
        for one in runs
    ]
    headers = (
        "Run",
        "Read over",
        "Hours of faults",
        "Faults",
        "Judged again, same verdicts",
    )
    return [*table(headers, body), ""]


def _latencies(rows: Sequence[Row]) -> list[str]:
    body = [
        (
            cell(one["check"]),
            cell(one["kind"]),
            cell(one["n"]),
            *(cell(one.get(k)) for k in ("min_s", "median_s", "p95_s", "max_s")),
            _within(one),
        )
        for one in _of(rows, "latency")
        if one["n"]
    ]
    headers = (
        "Check",
        "Fault kind",
        "Faults",
        "Min, s",
        "Median, s",
        "p95, s",
        "Max, s",
        "Within SC-5's bound",
    )
    platform = [
        (
            cell(one["kind"]),
            cell(one["check"]),
            f"{one['passed']} passed, {one['failed']} failed,"
            f" {one['not_applicable']} not applicable",
        )
        for one in _of(rows, "platform_check")
    ]
    return [
        "**Seconds each timed check measured**: detected, fault to offline;"
        " replanned, offline to the first revocation; alerted, fault to"
        " `StationOffline` firing",
        "",
        *table(headers, body),
        "",
        "**Faults done to the platform itself**",
        "",
        *table(("Fault kind", "Check", "Answers"), platform),
        "",
    ]


def _within(one: Row) -> str:
    share = _table(one.get("within_share"))
    if not share:
        return "—"
    return f"{one['within']} of {one['n']}, {cell(share['estimate'])} {_span(share)}"


def _sc5(sc5: Row) -> list[str]:
    if sc5["status"] != "measured":
        return [f"**SC-5** (simulated): not measured — {sc5['reason']}.", ""]
    return [
        f"**SC-5** (simulated): {sc5['within']} of {sc5['n']} detections within"
        f" {sc5['target_s']} s; the slowest took {cell(sc5['max_s'])} s. Every"
        f" one within: {cell(sc5['all_within'])}.",
        "",
    ]
