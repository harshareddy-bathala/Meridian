"""The orbit-uncertainty section of ``report.md``, and its figures, from orbit.jsonl.

Reference: docs/DECISIONS.md D-025, D-100, D-239; ``EVALUATION.md`` §6.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from meridian.reports.markdown import cell, table
from meridian.reports.svg_scatter import timing_scatter

__all__ = ["orbit_figure_name", "orbit_figures", "render_orbit"]

Row = Mapping[str, object]

_TITLES = {"measured": "Measured passes", "simulated": "Simulated passes"}


def orbit_figure_name(population: str) -> str:
    """The file a population's timing figure is written to."""
    return f"orbit_timing_{population}.svg"


def orbit_figures(rows: Sequence[Row]) -> dict[str, bytes]:
    """One timing figure per population that has a corrected error to plot."""
    figures = {}
    for population in _TITLES:
        points = _plotted(rows, population)
        if not points:
            continue
        counts = _counts(rows, population)
        figures[orbit_figure_name(population)] = timing_scatter(
            (
                f"Timing error against element-set age, {population}",
                f"{counts['kept']} kept of {counts['detections']} detections",
            ),
            points,
            [one for one in _of(rows, "regime") if one["population"] == population],
            _of(rows, "prior"),
            simulated=population == "simulated",
        )
    return figures


def render_orbit(rows: Sequence[Row]) -> list[str]:
    """The section's lines, without newlines."""
    lines = [
        "## Orbit uncertainty",
        "",
        "Timing error is when a station first heard a pass against when the"
        " platform predicted it would rise, the station's clock corrected by the"
        " offset its nearest heartbeat reported: `first_detection_at +"
        " clock_offset_s − aos` (D-025). §6.1's exclusions are counted, not"
        " hidden, and measured and simulated passes are never pooled. SC-3 is"
        " read from measured passes only (`EVALUATION.md` §6).",
        "",
        *_sc3(_of(rows, "sc3")[0]),
    ]
    for population, title in _TITLES.items():
        lines.extend(_population(rows, population, title))
        name = orbit_figure_name(population)
        if _plotted(rows, population):  # exactly when orbit_figures draws one
            lines.extend(
                [f"![Timing error against element-set age, {population}]({name})", ""]
            )
    return lines


def _plotted(rows: Sequence[Row], population: str) -> list[Row]:
    """A population's detections with a corrected error: its figure's points."""
    return [
        one
        for one in _of(rows, "detection")
        if one["population"] == population and one["error_s"] is not None
    ]


def _of(rows: Sequence[Row], kind: str) -> list[Row]:
    return [one for one in rows if one["row"] == kind]


def _counts(rows: Sequence[Row], population: str) -> Row:
    return next(
        one for one in _of(rows, "detections") if one["population"] == population
    )


def _span(value: object) -> str:
    if not isinstance(value, Mapping):
        return "—"
    return f"[{value['low']:.3f}, {value['high']:.3f}]"


def _sc3(sc3: Row) -> list[str]:
    if sc3["status"] != "measured":
        return ["### SC-3", "", f"Not measured: {sc3['reason']}.", ""]
    return [
        "### SC-3",
        "",
        f"Of the {sc3['n']} measured passes §6.1 keeps, a share of"
        f" {cell(sc3['coverage'])} had a timing error inside their stated 1σ,"
        f" 95% interval {_span(sc3['interval'])}. SC-3's target is"
        f" {cell(sc3['target'])}. The point estimate meets it:"
        f" {cell(sc3['point_meets'])}. The whole interval"
        f" is above it: {cell(sc3['interval_above'])}.",
        "",
    ]


def _population(rows: Sequence[Row], population: str, title: str) -> list[str]:
    counts = _counts(rows, population)
    heading = f"### {title}" + (" — SIMULATED" if population == "simulated" else "")
    if not counts["detections"]:
        return [heading, "", "No detections.", ""]
    facts = [
        ("detections", cell(counts["detections"])),
        ("clock offset unknown, excluded", cell(counts["clock_offset_unknown"])),
        (
            "inside clock uncertainty, excluded",
            cell(counts["within_clock_uncertainty"]),
        ),
        ("kept", cell(counts["kept"])),
        ("1σ as issued with the assignment", cell(counts["sigma_from_assignment"])),
        ("1σ from the published prior", cell(counts["sigma_from_prior"])),
    ]
    return [
        heading,
        "",
        *table(("", "Passes"), facts),
        "",
        *_regimes(rows, population),
        *_coverage(rows, population),
        *_spread(rows, population),
    ]


def _regimes(rows: Sequence[Row], population: str) -> list[str]:
    body = [
        (
            cell(one["regime"]),
            cell(one["n"]),
            cell(one["station_days"]),
            f"{cell(one['age_min_days'])} to {cell(one['age_max_days'])}",
            cell(one["mean_abs_error_s"]),
            _slope(one),
            cell(one["intercept_s"]),
        )
        for one in _of(rows, "regime")
        if one["population"] == population
    ]
    if not body:
        return ["No pass is kept, so no regime has a slope.", ""]
    headers = (
        "Regime",
        "Passes",
        "Station-days",
        "Ages, days",
        "Mean |error|, s",
        "Slope, s per day [95%]",
        "Intercept, s",
    )
    return [
        "**|Timing error| against element-set age**, least squares by regime",
        "",
        *table(headers, body),
        "",
    ]


def _slope(one: Row) -> str:
    if one["slope_s_per_day"] is None:
        return f"none: {one.get('reason', 'undefined')}"
    return f"{cell(one['slope_s_per_day'])} {_span(one['slope_interval'])}"


def _coverage(rows: Sequence[Row], population: str) -> list[str]:
    coverage = next(
        one for one in _of(rows, "coverage") if one["population"] == population
    )

    def said(found: object) -> str:
        if not isinstance(found, Mapping):
            return "—"
        return (
            f"{found['within']} of {found['n']}, {cell(found['estimate'])}"
            f" {_span(found)}"
        )

    return [
        f"Inside the stated 1σ: {said(coverage['kept'])} under §6.1's exclusions;"
        f" {said(coverage['offset_known'])} with only unknown offsets left out."
        " §6.1 discards an error smaller than the clock's uncertainty, which is"
        " inside any 1σ, so the two bound that rule's effect (D-239).",
        "",
    ]


def _spread(rows: Sequence[Row], population: str) -> list[str]:
    one = next(row for row in _of(rows, "spread") if row["population"] == population)
    if one["verdict"] == "not tested":
        said = (
            f"not tested: {one['young']} detections on element sets under a day"
            f" old, against a minimum of {one['min_young']} stated in advance"
        )
    else:
        said = (
            f"first detection on element sets under a day old spreads by"
            f" {cell(one['spread_s'])} s (standard deviation, {one['young']}"
            f" passes), against the {cell(one['signal_s'])} s the orbit could move"
            f" it over the ages present at {one['signal_s_per_day']} s a day:"
            f" §6.1 is **{one['verdict']}**"
        )
    return [f"§6.3's test (D-100): {said}.", ""]
