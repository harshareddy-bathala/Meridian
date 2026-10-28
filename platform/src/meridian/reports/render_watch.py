"""The receive-chain watch section of ``report.md``, from watch.jsonl.

Reference: docs/DECISIONS.md D-105, D-294, D-296; ``EVALUATION.md`` §11.3.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from meridian.reports.markdown import cell, rate, table

__all__ = ["render_watch"]

Row = Mapping[str, object]

_FIRST = {
    "warning": "warning first",
    "failure": "failure first",
    "neither": "neither, by the end",
}


def render_watch(rows: Sequence[Row]) -> list[str]:
    """The section's lines, without newlines."""
    (runs,) = _of(rows, "runs")
    (sc9,) = _of(rows, "sc9")
    lines = [
        "## Receive-chain watch",
        "",
        "Each station's receive chain is compared with its own signal at each"
        " elevation, and warned about when it stays short across elevations"
        " (`EVALUATION.md` §11.3, D-294). SC-9 is measured on **simulated**"
        " fleets: each flies a clean stretch first, so every chain has a"
        " baseline, then a gradual degradation whose onset and rate only the"
        " simulator's ledger holds (D-105, D-296).",
        "",
        "**What it claims, and what it does not.** A simulated chain's loss of"
        " signal, of the shape the simulator gives it, is warned about. It does"
        " not claim to locate which part of a chain failed, nor to detect a"
        " fault that leaves signal against elevation unchanged.",
        "",
    ]
    if sc9["status"] != "measured":
        return [*lines, "Not measured: no sealed watch run was given.", ""]
    return [
        *lines,
        *_runs(runs),
        *_degradations(rows),
        *_summary(_of(rows, "summary")[0]),
        *_alarms(_of(rows, "false_alarms")[0]),
        *_verdict(sc9),
    ]


def _runs(runs: Row) -> list[str]:
    held = runs["runs"]
    listed = held if isinstance(held, list) else []
    return [
        f"From {len(listed)} sealed runs, every row simulated:",
        "",
        *table(
            ("Run", "Scenario", "Seed", "Stations", "Watched from", "End"),
            [
                (
                    cell(one["run"]),
                    cell(one["scenario"]),
                    cell(one["master_seed"]),
                    cell(one["stations"]),
                    cell(one["watched_from"]),
                    cell(one["end"]),
                )
                for one in listed
            ],
        ),
        "",
    ]


def _degradations(rows: Sequence[Row]) -> list[str]:
    found = _of(rows, "degradation")
    if not found:
        return ["No degradation run was given.", ""]
    return [
        "### Each degradation (simulated)",
        "",
        "Delay is onset to warning, with the loss the chain had by then; lead is"
        " warning to the first pass the degradation cost, a decode the clean"
        " outcome would have made. A positive lead is a warning that came first.",
        "",
        *table(
            ("Run", "Station", "dB/day", "Delay h", "Loss dB", "Lead h", "First"),
            [
                (
                    cell(one["run"]),
                    cell(one["station_index"]),
                    cell(one["rate_db_per_day"]),
                    cell(one["delay_h"]),
                    cell(one["loss_db"]),
                    cell(one["lead_h"]),
                    _FIRST[str(one["first"])],
                )
                for one in found
            ],
        ),
        "",
    ]


def _summary(summary: Row) -> list[str]:
    def spread(key: str) -> str:
        held = summary[key]
        values = held if isinstance(held, Mapping) else {}
        low, middle, high = (cell(values.get(k)) for k in ("min", "median", "max"))
        return f"{low} / {middle} / {high}"

    return [
        "### Delay and lead (simulated)",
        "",
        *table(
            ("Measure", "Min / median / max"),
            [
                ("delay, hours", spread("delay_h")),
                ("loss at the warning, dB", spread("loss_db")),
                ("lead, hours", spread("lead_h")),
            ],
        ),
        "",
        f"Warned before the first failure in {cell(summary['warned_first'])} of"
        f" {cell(summary['decided'])} degradations decided by the run's end"
        f" ({rate(summary['warned_first_share'])});"
        f" {cell(summary['neither'])} had neither by then and are left out.",
        "",
    ]


def _alarms(alarms: Row) -> list[str]:
    interval = alarms["interval"]
    bounds = (
        f" [{cell(interval.get('low'))}, {cell(interval.get('high'))}]"
        if isinstance(interval, Mapping)
        else ""
    )
    return [
        "### False alarms (simulated)",
        "",
        f"{cell(alarms['warnings'])} warnings over {cell(alarms['station_days'])}"
        f" station-days of fault-free flying, from {cell(alarms['runs'])} runs:"
        f" {cell(alarms['per_station_30_days'])} per station per 30 days{bounds}"
        " (exact Poisson, 95%).",
        "",
    ]


def _verdict(sc9: Row) -> list[str]:
    met = "met" if sc9["met"] else "not met"
    return [
        "### SC-9 (simulated)",
        "",
        *table(
            ("Measure", "Value", "Target", "Met"),
            [
                (
                    "warned before the first failure",
                    cell(sc9["warned_first"]),
                    f"≥ {cell(sc9['warned_first_target'])}",
                    "yes" if sc9["warned_first_met"] else "no",
                ),
                (
                    "false alarms per station per 30 days",
                    cell(sc9["false_alarms"]),
                    f"≤ {cell(sc9['false_alarm_target'])}",
                    "yes" if sc9["false_alarms_met"] else "no",
                ),
            ],
        ),
        "",
        f"SC-9 against its proposed targets: **{met}**.",
        "",
    ]


def _of(rows: Sequence[Row], kind: str) -> list[Row]:
    return [one for one in rows if one["row"] == kind]
