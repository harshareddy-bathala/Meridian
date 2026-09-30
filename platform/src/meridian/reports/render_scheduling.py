"""The scheduling section of ``report.md``, and its figures, from ``scheduling.jsonl``.

Reference: docs/DECISIONS.md D-172, D-235, D-238; ``EVALUATION.md`` §3.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from meridian.reports.markdown import cell, table
from meridian.reports.svg_intervals import Estimate, interval_chart

__all__ = ["GAINS_FIGURE", "REGRET_FIGURE", "render_scheduling", "scheduling_figures"]

GAINS_FIGURE = "scheduling_gains.svg"
REGRET_FIGURE = "scheduling_regret.svg"

Row = Mapping[str, object]


def scheduling_figures(rows: Sequence[Row]) -> dict[str, bytes]:
    """The gains against the SC-1 target, and every scheduler's lead by the oracle."""
    replay = _of(rows, "replay")[0]
    if replay["status"] != "replayed":
        return {}
    sample = f"{replay['station_days']} station-days, {replay['resamples']} resamples"
    # A gain with no value (the second scheduler decoded nothing) is left out
    # of the figure, never drawn as zero; its table row says why.
    gains = [
        _estimate(_gain_label(one), one, "relative")
        for one in _of(rows, "gain")
        if one["relative"] is not None
    ]
    regret = [
        _estimate(str(one["scheduler"]), one, "per_hour") for one in _of(rows, "regret")
    ]
    return {
        GAINS_FIGURE: interval_chart(
            ("Gain in decoded frames per station-hour", sample),
            gains,
            unit="relative to the second scheduler",
            target=(float(str(_of(rows, "sc1")[0]["target"])), "SC-1 target"),
        ),
        REGRET_FIGURE: interval_chart(
            ("How far each scheduler falls short of the oracle", sample),
            regret,
            unit="decoded frames per station-hour below the oracle",
        ),
    }


def render_scheduling(rows: Sequence[Row]) -> list[str]:
    """The section's lines, without newlines."""
    lines = [
        "## Scheduling",
        "",
        "Every retained station-day of the test span is one problem, and seven"
        " schedulers solve it with the same candidates, constraints, solver and"
        " time limit: greedy A and greedy B, existing practice; the optimiser"
        " under A to D, each valuing a pass by its configuration's model; and"
        " the oracle, valuing each pass by what it decoded (D-172). A pass whose"
        " outcome nobody knows adds no frames and is counted, never imputed."
        " Intervals are 95% paired bootstraps over station-days.",
        "",
    ]
    replay = _of(rows, "replay")[0]
    if replay["status"] != "replayed":
        return [*lines, f"Not replayed: {replay['reason']}. SC-1 is not measured.", ""]
    return [
        *lines,
        *_sc1(_of(rows, "sc1")[0]),
        *_replayed(replay),
        *_schedulers(rows),
        *_gains(rows),
        f"![Gains against the SC-1 target]({GAINS_FIGURE})",
        "",
        *_regret(rows),
        f"![How far each scheduler falls short of the oracle]({REGRET_FIGURE})",
        "",
        "Solver runtimes and HiGHS's version are measured, not derived, and are"
        " in `manifest.json` under `environment` (D-235).",
        "",
    ]


def _of(rows: Sequence[Row], kind: str) -> list[Row]:
    return [one for one in rows if one["row"] == kind]


def _span(value: object) -> str:
    if not isinstance(value, Mapping):
        return "—"
    return f"[{value['low']:.3f}, {value['high']:.3f}]"


def _gain_label(one: Row) -> str:
    named = " (SC-1)" if one["label"] == "SC-1" else ""
    return f"{one['first']} − {one['second']}{named}"


def _estimate(label: str, one: Row, key: str) -> Estimate:
    interval = one[f"{key}_interval"]
    value = one[key]
    bounds = (
        (float(str(interval["low"])), float(str(interval["high"])))
        if isinstance(interval, Mapping)
        else (None, None)
    )
    return Estimate(label, float(str(value)), *bounds)


def _sc1(sc1: Row) -> list[str]:
    if sc1["status"] != "measured":
        return ["### SC-1", "", f"Not measured: {sc1['reason']}.", ""]
    return [
        "### SC-1",
        "",
        f"Optimised D decodes {cell(sc1['relative'])} more frames per"
        f" station-hour than optimised B, relative to B, 95% interval"
        f" {_span(sc1['interval'])}. SC-1's target is {cell(sc1['target'])}. The"
        f" point estimate meets it: {cell(sc1['point_meets'])}. The whole"
        f" interval is above it: {cell(sc1['interval_above'])}.",
        "",
    ]


def _replayed(replay: Row) -> list[str]:
    left = replay["left_out"]
    left_out = (
        " · ".join(f"{name} {count}" for name, count in sorted(left.items()))
        if isinstance(left, Mapping)
        else "—"
    )
    facts = [
        ("test span", f"{cell(replay['test_from'])} to {cell(replay['as_of'])}"),
        ("completeness threshold", cell(replay["threshold"])),
        ("station-days replayed", cell(replay["station_days"])),
        ("left out", left_out),
        ("candidate passes", cell(replay["candidates"])),
        ("with a known outcome", cell(replay["known_outcomes"])),
        ("station-hours", cell(replay["station_hours"])),
        ("frames term", cell(replay["frames_term"])),
        ("time limit per station-day, s", cell(replay["time_limit_s"])),
        ("turnaround, s", cell(replay["turnaround_s"])),
        ("solver seed", cell(replay["solver_seed"])),
        ("schedules checked", cell(replay["schedules_checked"])),
        ("constraint violations", cell(replay["violations"])),
    ]
    return ["### What was replayed", "", *table(("", "Value"), facts), ""]


def _schedulers(rows: Sequence[Row]) -> list[str]:
    body = []
    for one in _of(rows, "scheduler"):
        statuses = one["statuses"]
        solved = (
            " · ".join(f"{name} {count}" for name, count in sorted(statuses.items()))
            if isinstance(statuses, Mapping)
            else "—"
        )
        body.append(
            (
                cell(one["name"]),
                cell(one["selected"]),
                cell(one["frames"]),
                cell(one["per_hour"]),
                cell(one["of_oracle"]),
                cell(one["unknown_share"]),
                solved,
            )
        )
    headers = (
        "Scheduler",
        "Passes taken",
        "Frames",
        "Per station-hour",
        "Of oracle",
        "Unknown share",
        "Solved",
    )
    return ["### Schedulers", "", *table(headers, body), ""]


def _gains(rows: Sequence[Row]) -> list[str]:
    body = [
        (
            f"{one['first']} − {one['second']} ({one['label']})",
            f"{cell(one['per_hour'])} {_span(one['per_hour_interval'])}",
            f"{cell(one['relative'])} {_span(one['relative_interval'])}",
        )
        for one in _of(rows, "gain")
    ]
    headers = ("Gain", "Frames per station-hour [95%]", "Relative [95%]")
    return ["### Gains", "", *table(headers, body), ""]


def _regret(rows: Sequence[Row]) -> list[str]:
    shares = {one["name"]: one["of_oracle"] for one in _of(rows, "scheduler")}
    body = [
        (
            cell(one["scheduler"]),
            f"{cell(one['per_hour'])} {_span(one['per_hour_interval'])}",
            cell(shares.get(one["scheduler"])),
        )
        for one in _of(rows, "regret")
    ]
    headers = ("Scheduler", "Below the oracle, per station-hour [95%]", "Of oracle")
    return ["### Oracle regret", "", *table(headers, body), ""]
