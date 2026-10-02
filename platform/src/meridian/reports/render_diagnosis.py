"""The loss-diagnosis section of ``report.md``, from diagnosis.jsonl.

Reference: docs/DECISIONS.md D-105, D-270, D-278; ``EVALUATION.md`` §11.2.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from meridian.reports.markdown import cell, rate, table

__all__ = ["render_diagnosis"]

Row = Mapping[str, object]

_TRUTH_LABELS = {
    "control": "control (no category)",
    "acted_not_cause": "fault acted, not the cause",
    "several": "several faults acted",
    "none": "no fault (the outcome model)",
}


def render_diagnosis(rows: Sequence[Row]) -> list[str]:
    """The section's lines, without newlines."""
    (runs,) = _of(rows, "runs")
    (sc8,) = _of(rows, "sc8")
    lines = [
        "## Loss diagnosis",
        "",
        "Every failed or partial reception is given one cause — satellite"
        " silent, station not listening, obstruction, interference or a timing"
        " fault — or *undetermined* (`EVALUATION.md` §11.2). SC-8 is measured on"
        " **simulated** fleets whose injected causes the simulator's ledger"
        " holds and the platform never saw (D-105, D-278).",
        "",
        "**What it claims, and what it does not.** Given evidence of the shape"
        " a simulated fault produces, the diagnosis names that fault. It does"
        " not claim real-world diagnostic accuracy; the real cases below begin"
        " to answer that.",
        "",
    ]
    if not runs["reviewed"]:
        lines += [
            "**Fault effects not independently reviewed (D-270).** The"
            " specification these faults follow is pinned and owes the review"
            " D-105 asks for; until it is recorded, SC-8 is not claimed.",
            "",
        ]
    if sc8["status"] != "measured":
        return [
            *lines,
            "Not measured: no sealed diagnosis run was given.",
            "",
            *_real(rows),
        ]
    lines += _runs(runs)
    lines += _matrix(rows)
    lines += _recall(rows)
    lines += _fractions(_of(rows, "fractions")[0], sc8)
    lines += _real(rows)
    return lines


def _runs(runs: Row) -> list[str]:
    held = runs["runs"]
    listed = held if isinstance(held, list) else []
    return [
        f"From {len(listed)} sealed runs, every row simulated:",
        "",
        *table(
            ("Run", "Scenario", "Seed", "Stations", "Hours"),
            [
                (
                    cell(one["run"]),
                    cell(one["scenario"]),
                    cell(one["master_seed"]),
                    cell(one["stations"]),
                    cell(one["hours"]),
                )
                for one in listed
            ],
        ),
        "",
    ]


def _matrix(rows: Sequence[Row]) -> list[str]:
    cells = _of(rows, "cell")
    answers = list(dict.fromkeys(str(one["diagnosed"]) for one in cells))
    truths = list(dict.fromkeys(str(one["truth"]) for one in cells))
    count = {(str(one["truth"]), str(one["diagnosed"])): one["count"] for one in cells}
    return [
        "### Confusion matrix (simulated)",
        "",
        "Rows are what truly lost the pass, decided from the ledger and the clean"
        " outcome recomputed from the seed; columns are what was diagnosed.",
        "",
        *table(
            ("Truth", *answers),
            [
                (
                    _TRUTH_LABELS.get(truth, truth),
                    *(cell(count[truth, a]) for a in answers),
                )
                for truth in truths
            ],
        ),
        "",
    ]


def _recall(rows: Sequence[Row]) -> list[str]:
    spread = {str(one["cause"]): one for one in _of(rows, "spread")}
    return [
        "### Recall per cause (simulated)",
        "",
        "A recall over a handful of cases is shown as that: the count is beside"
        " it, and the spread is across runs that had a case.",
        "",
        *table(
            ("Cause", "Cases", "Named", "Recall [95%]", "Runs", "Min", "Median", "Max"),
            [
                (
                    cell(one["cause"]),
                    cell(one["cases"]),
                    cell(one["named"]),
                    rate(one["recall"]),
                    cell(spread[str(one["cause"])]["runs"]),
                    cell(spread[str(one["cause"])]["min"]),
                    cell(spread[str(one["cause"])]["median"]),
                    cell(spread[str(one["cause"])]["max"]),
                )
                for one in _of(rows, "recall")
            ],
        ),
        "",
    ]


def _fractions(fractions: Row, sc8: Row) -> list[str]:
    met = "met" if sc8["met"] else "not met"
    claim = "and claimable" if sc8["claimable"] else "and not claimed (D-270)"
    return [
        "### SC-8 (simulated)",
        "",
        *table(
            ("Measure", "Value", "Target"),
            [
                (
                    "wrong cause",
                    f"{cell(fractions['wrong_fraction'])}"
                    f" ({cell(fractions['wrong'])} of {cell(fractions['diagnoses'])})",
                    f"≤ {cell(sc8['wrong_target'])}",
                ),
                (
                    "undetermined",
                    f"{cell(fractions['undetermined_fraction'])}"
                    f" ({cell(fractions['undetermined'])} of"
                    f" {cell(fractions['diagnoses'])})",
                    "reported apart",
                ),
                ("causes with no case", cell(sc8["causes_without_cases"]), "none"),
                ("causes below recall", cell(sc8["causes_below_target"]), "none"),
            ],
        ),
        "",
        f"SC-8 against its proposed targets: **{met}**, {claim}.",
        "",
    ]


def _real(rows: Sequence[Row]) -> list[str]:
    real = _of(rows, "real")
    return [
        "### Real cases (measured, apart)",
        "",
        "Diagnoses of measured stations' losses in the snapshot, never pooled with"
        " the simulated matrix. None has a known cause yet, so none is scored.",
        "",
        *table(
            ("Cause", "Diagnosed", "Labelled"),
            [
                (cell(one["cause"]), cell(one["diagnosed"]), cell(one["labelled"]))
                for one in real
            ],
        ),
        "",
    ]


def _of(rows: Sequence[Row], kind: str) -> list[Row]:
    return [one for one in rows if one["row"] == kind]
