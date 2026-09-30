"""The prediction section of ``report.md``, and its figures, from ``prediction.jsonl``.

Reference: docs/DECISIONS.md D-235, D-237; ``EVALUATION.md`` §3, §7, §8.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from meridian.reports.markdown import cell, rate, table
from meridian.reports.svg import reliability_diagram

__all__ = ["figure_name", "prediction_figures", "render_prediction"]

Row = Mapping[str, object]

_SHOWN = {"A": "A (B's model)", "C": "C", "D": "D", "D-conditions": "D∖conditions"}


def figure_name(model: object) -> str:
    """The file a model's reliability diagram is written to."""
    return f"reliability_{str(model).lower().replace('-', '_')}.svg"


def prediction_figures(rows: Sequence[Row]) -> dict[str, bytes]:
    """One reliability diagram per fitted model."""
    return {
        figure_name(one["name"]): reliability_diagram(
            f"Configuration {_SHOWN.get(str(one['name']), one['name'])}",
            f"test span n {one['test']} · Brier {cell(one['brier'])}"
            f" against the base rate's {cell(one['base_brier'])}",
            [bin_ for bin_ in _of(rows, "bin") if bin_["model"] == one["name"]],
            simulated=False,
        )
        for one in _fitted(rows)
    }


def render_prediction(rows: Sequence[Row]) -> list[str]:
    """The section's lines, without newlines."""
    lines = [
        "## Prediction",
        "",
        "Each configuration is fitted on measured passes only (D-078), judged on"
        " the test span after its validation date against the training span's"
        " base rate, and published under `models/` as `meridian model fit` would"
        " publish it. B's probabilities are A's: B differs in the scheduler's"
        " objective, not its model (D-160). Every interval is a 95% bootstrap"
        " that resamples whole station-days.",
        "",
        *_sc2(rows),
        *_summary(rows),
        *_splits(rows),
        *_comparisons(rows),
        *_conditions(rows),
    ]
    for one in _fitted(rows):
        lines.extend(_model(rows, one))
    return lines


def _of(rows: Sequence[Row], kind: str) -> list[Row]:
    return [one for one in rows if one["row"] == kind]


def _fitted(rows: Sequence[Row]) -> list[Row]:
    return [one for one in _of(rows, "model") if one["status"] == "fitted"]


def _span(value: object) -> str:
    if not isinstance(value, Mapping):
        return "—"
    return f"[{value['low']:.3f}, {value['high']:.3f}]"


def _sc2(rows: Sequence[Row]) -> list[str]:
    sc2 = _of(rows, "sc2")[0]
    if sc2["status"] != "measured":
        return ["### SC-2", "", f"Not measured: {sc2['reason']}.", ""]
    return [
        "### SC-2",
        "",
        f"D's Brier score is lower than the base rate's by {cell(sc2['skill'])}"
        f" of it, 95% interval {_span(sc2['interval'])}. SC-2's target is"
        f" {cell(sc2['target'])}. The point estimate meets it:"
        f" {cell(sc2['point_meets'])}. The whole interval is above it:"
        f" {cell(sc2['interval_above'])}.",
        "",
    ]


def _summary(rows: Sequence[Row]) -> list[str]:
    body = []
    for one in _of(rows, "model"):
        name = _SHOWN.get(str(one["name"]), str(one["name"]))
        if one["status"] != "fitted":
            body.append((name, f"not fitted: {cell(one['reason'])}", *("—",) * 6))
            continue
        body.append(
            (
                name,
                "fitted",
                f"{one['test']} ({one['test_decoded']} decoded)",
                cell(one["station_days"]),
                f"{cell(one['brier'])} {_span(one['brier_interval'])}",
                f"{cell(one['base_brier'])} at rate {cell(one['base_rate'])}",
                f"{cell(one['skill'])} {_span(one['skill_interval'])}",
                _folds(one),
            )
        )
    headers = (
        "Model",
        "Status",
        "Test passes",
        "Station-days",
        "Brier [95%]",
        "Base-rate Brier",
        "Skill [95%]",
        "Folds",
    )
    return ["### Models", "", *table(headers, body), ""]


def _folds(one: Row) -> str:
    if one["fold_brier_mean"] is None:
        return f"none of {one['folds']} fitted"
    spread = "" if one["fold_brier_sd"] is None else f" ± {cell(one['fold_brier_sd'])}"
    return (
        f"Brier {cell(one['fold_brier_mean'])}{spread},"
        f" {one['folds_fitted']} of {one['folds']}"
    )


def _splits(rows: Sequence[Row]) -> list[str]:
    body = [
        (
            _SHOWN.get(str(one["name"]), str(one["name"])),
            cell(one["seed"]),
            cell(one["train_until"]),
            cell(one["validate_until"]),
            cell(one["as_of"]),
            f"{one['train']} · {one['validate']} · {one['test']}",
            cell(one["simulated_left_out"]),
            f"`{str(one['model_sha256'])[:12]}`",
        )
        for one in _fitted(rows)
    ]
    headers = (
        "Model",
        "Seed",
        "Train until",
        "Validate until",
        "As of",
        "Train · validate · test",
        "Simulated, left out",
        "Model",
    )
    if not body:
        return []
    return ["**Temporal splits** (`EVALUATION.md` §8)", "", *table(headers, body), ""]


def _comparisons(rows: Sequence[Row]) -> list[str]:
    body = []
    for one in _of(rows, "comparison"):
        pair = (
            f"{_SHOWN.get(str(one['first']), one['first'])} against"
            f" {_SHOWN.get(str(one['second']), one['second'])}"
        )
        if one["status"] != "compared":
            body.append((pair, cell(one["question"]), "—", "—", cell(one["reason"])))
            continue
        body.append(
            (
                pair,
                cell(one["question"]),
                cell(one["n"]),
                cell(one["station_days"]),
                f"{cell(one['reduction'])} {_span(one['interval'])}",
            )
        )
    headers = ("Comparison", "Asks", "Passes", "Station-days", "Brier lower by [95%]")
    return [
        "### Comparisons",
        "",
        "How much lower the first model's Brier score is than the second's, on"
        " the same test passes. Above zero, the first is better.",
        "",
        *table(headers, body),
        "",
    ]


def _conditions(rows: Sequence[Row]) -> list[str]:
    one = _of(rows, "conditions")[0]
    lines = ["### Public conditions", ""]
    if "reason" in one:
        return [*lines, f"Kp is untested: {one['reason']}.", ""]
    verdict = (
        "so D against D∖conditions can speak to it"
        if one["kp_verdict"] == "measurable"
        else "so it is **untested**: this sample cannot say whether it helps"
    )
    return [
        *lines,
        f"Kp: {one['disturbed']} of the {one['kp_known']} test passes with a"
        f" published Kp were disturbed (Kp ≥ {one['disturbed_kp']:g}), against a"
        f" minimum of {one['min_disturbed']} stated in advance, {verdict}"
        " (`EVALUATION.md` §3). Cloud cover was published before"
        f" {one['cloud_known']} of {one['test_passes']} test passes.",
        "",
    ]


def _model(rows: Sequence[Row], one: Row) -> list[str]:
    name = one["name"]
    shown = _SHOWN.get(str(name), str(name))
    mine = [row for row in rows if row.get("model") == name]
    bins = [
        (
            f"{row['low']:.1f}–{row['high']:.1f}",
            cell(row["n"]),
            cell(row["mean_predicted"]),
            rate(row["observed"]),
        )
        for row in mine
        if row["row"] == "bin"
    ]
    segments = [
        (
            cell(row["dimension"]),
            cell(row["value"]),
            cell(row["n"]),
            cell(row["brier"]),
            cell(row["mean_predicted"]),
            rate(row["observed"]),
        )
        for row in mine
        if row["row"] == "segment"
    ]
    routes = [
        (cell(row["path"]), cell(row["n"]), cell(row["brier"]))
        for row in mine
        if row["row"] == "route"
    ]
    return [
        f"### Configuration {shown}",
        "",
        f"![Reliability diagram, configuration {shown}]({figure_name(name)})",
        "",
        *table(("Predicted", "Passes", "Mean predicted", "Observed [95%]"), bins),
        "",
        "**By segment**",
        "",
        *table(
            ("Dimension", "Value", "Passes", "Brier", "Mean predicted", "Observed"),
            segments,
        ),
        "",
        "**Routes** — a station below its history minimum is scored by the"
        " geometry-only model (cold start, D-161)",
        "",
        *table(("Route", "Passes", "Brier"), routes),
        "",
        *_fold_table(mine),
    ]


def _fold_table(mine: Sequence[Row]) -> list[str]:
    folds = [
        (
            cell(row["train_until"]),
            cell(row["validate_until"]),
            cell(row["as_of"]),
            cell(row["n"]),
            cell(row["brier"]),
            cell(row["base_brier"]),
            cell(row["refused"]),
        )
        for row in mine
        if row["row"] == "fold"
    ]
    if not folds:
        return []
    headers = (
        "Train until",
        "Validate until",
        "Judged until",
        "Passes",
        "Brier",
        "Base-rate Brier",
        "Not fitted because",
    )
    return ["**Rolling-origin folds** (D-162)", "", *table(headers, folds), ""]
