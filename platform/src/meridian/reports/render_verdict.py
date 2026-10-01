"""The reception-verdict section of ``report.md``, and its figures, from verdict.jsonl.

Reference: docs/DECISIONS.md D-260, D-262, D-264; ``EVALUATION.md`` §11.1.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from meridian.reports.markdown import cell, rate, table
from meridian.reports.svg import reliability_diagram

__all__ = ["render_verdict", "verdict_figure_name", "verdict_figures"]

Row = Mapping[str, object]

_SUBSETS = {
    "every": "every labelled reception",
    "rated": "rated receptions only",
}


def verdict_figure_name(subset: str) -> str:
    """The file a subset's reliability diagram is written to."""
    return f"verdict_reliability_{subset}.svg"


def verdict_figures(rows: Sequence[Row]) -> dict[str, bytes]:
    """One reliability diagram per judged subset; none when not measured."""
    return {
        verdict_figure_name(str(one["subset"])): reliability_diagram(
            f"Reception verdict, {_SUBSETS[str(one['subset'])]}",
            f"test span n {one['n']} · Brier {cell(one['brier'])}"
            f" against the base rate's {cell(one['base_brier'])}",
            [bin_ for bin_ in _of(rows, "bin") if bin_["subset"] == one["subset"]],
            simulated=False,
        )
        for one in _of(rows, "score")
    }


def render_verdict(rows: Sequence[Row]) -> list[str]:
    """The section's lines, without newlines."""
    lines = [
        "## Reception verdict",
        "",
        "The verdict is a calibrated probability that a reception is usable,"
        " read from its outcome, SNR, frames decoded against frames expected,"
        " decoder and listening evidence (`EVALUATION.md` §11.1). Its label is a"
        " person's rating of the decoded product, made blind to the verdict; a"
        " reception with no product is unusable without one (D-260). It is"
        " fitted and judged on measured receptions only (D-078).",
        "",
    ]
    (summary,) = _of(rows, "verdict")
    if summary["status"] != "measured":
        return [*lines, f"Not measured: {summary['reason']}.", ""]
    lines.extend(_summary(summary))
    lines.extend(_sc7(_of(rows, "sc7")[0]))
    for one in _of(rows, "score"):
        lines.extend(_subset(rows, one))
    lines.extend(_segments(rows))
    lines.extend(_threshold(rows, summary))
    return lines


def _summary(summary: Row) -> list[str]:
    return [
        *table(
            ("Verdict", "Value"),
            [
                ("method", cell(summary["method"])),
                ("rubric", cell(summary["rubric"])),
                ("partial below", cell(summary["partial_below"])),
                (
                    "split",
                    f"train until {cell(summary['train_until'])}, validate until"
                    f" {cell(summary['validate_until'])}, test until"
                    f" {cell(summary['as_of'])}",
                ),
                (
                    "receptions",
                    f"train {summary['train']} · validate {summary['validate']}"
                    f" · test {summary['test']}",
                ),
                (
                    "left out",
                    f"{summary['simulated']} simulated · {summary['unrated']}"
                    f" unrated · {summary['other_rubric']} under another rubric",
                ),
            ],
        ),
        "",
    ]


def _sc7(sc7: Row) -> list[str]:
    if sc7["status"] != "measured":
        return ["### SC-7", "", f"Not measured: {sc7['reason']}.", ""]
    return [
        "### SC-7",
        "",
        f"On {sc7['n']} rated test receptions, the verdict's Brier score is lower"
        f" than the base rate's by {cell(sc7['skill'])} of it, 95% interval"
        f" {_span(sc7['interval'])}. SC-7's proposed target is"
        f" {cell(sc7['target'])}. The point estimate meets it:"
        f" {cell(sc7['point_meets'])}. The whole interval is above it:"
        f" {cell(sc7['interval_above'])}.",
        "",
    ]


def _subset(rows: Sequence[Row], score: Row) -> list[str]:
    subset = str(score["subset"])
    bins = [
        (
            f"{row['low']:.1f}–{row['high']:.1f}",
            cell(row["n"]),
            cell(row["mean_predicted"]),
            rate(row["observed"]),
        )
        for row in _of(rows, "bin")
        if row["subset"] == subset
    ]
    routes = [
        (cell(row["route"]), cell(row["n"]), cell(row["brier"]))
        for row in _of(rows, "route")
        if row["subset"] == subset
    ]
    return [
        f"### Judged on {_SUBSETS[subset]}",
        "",
        f"Test span n {score['n']}, {score['usable']} usable. Brier"
        f" {cell(score['brier'])} against {cell(score['base_brier'])} for the"
        f" training span's usable rate {cell(score['base_rate'])}. Skill"
        f" {cell(score['skill'])}, 95% interval {_span(score['interval'])},"
        " resampling whole station-days.",
        "",
        f"![Reliability diagram, {_SUBSETS[subset]}]({verdict_figure_name(subset)})",
        "",
        *table(("Predicted", "Receptions", "Mean predicted", "Observed [95%]"), bins),
        "",
        "**Routes** — a reception without an SNR or decoder statistics is scored"
        " by a model fitted without them (D-261)",
        "",
        *table(("Route", "Receptions", "Brier"), routes),
        "",
    ]


def _segments(rows: Sequence[Row]) -> list[str]:
    body = [
        (
            cell(row["dimension"]),
            cell(row["value"]),
            cell(row["n"]),
            cell(row["brier"]),
            cell(row["mean_predicted"]),
            rate(row["observed"]),
        )
        for row in _of(rows, "segment")
    ]
    return [
        "### By segment, every labelled reception",
        "",
        *table(
            ("Dimension", "Value", "Receptions", "Brier", "Mean predicted", "Usable"),
            body,
        ),
        "",
        "Archive receptions carry no rating, so the archive segment §11.1 names"
        " is empty.",
        "",
    ]


def _threshold(rows: Sequence[Row], summary: Row) -> list[str]:
    body = [
        (cell(row["side"]), cell(row["n"]), rate(row["usable"]))
        for row in _of(rows, "threshold")
    ]
    return [
        "### Partial threshold",
        "",
        f"Decoded validation receptions either side of"
        f" {cell(summary['partial_below'])}, the verdict below which a decoded"
        " reception counts as partial. Read on validation, never on test (D-262).",
        "",
        *table(("Side", "Receptions", "Usable"), body),
        "",
    ]


def _of(rows: Sequence[Row], kind: str) -> list[Row]:
    return [one for one in rows if one["row"] == kind]


def _span(value: object) -> str:
    if not isinstance(value, Mapping):
        return "—"
    return f"[{value['low']:.3f}, {value['high']:.3f}]"
