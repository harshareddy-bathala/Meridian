"""The loss-diagnosis section: SC-8, from sealed simulated fleets, and real cases apart.

*Loss diagnosis names the injected cause: at least 80% recall for each of the
five causes, and at most 5% of diagnoses naming a wrong cause, on simulated
faults* (``EVALUATION.md`` §11.2) — proposed, to agree with the team.

Every diagnosis of every sealed run (``meridian.datasets.diagnosis_runs``) is
set beside its truth (:mod:`meridian.reports.diagnosis_truth`), and the section
reports, from those pairs alone:

* **the confusion matrix**, every truth by every answer, *undetermined*
  included, zeros written;
* **recall per cause**, with its case count and a Wilson interval, and the
  spread between runs;
* **the wrong-cause fraction**: diagnoses naming a cause the truth does not
  allow. A cause is allowed when it is the truth, or when it is the cause of a
  fault that acted on the pass (``acted_not_cause``, ``several``): naming a
  fault that was there is not naming a wrong one. *Undetermined* is never wrong;
* **the undetermined fraction**, reported apart, never folded into either;
* **real cases**, from the raw snapshot's measured diagnoses, in their own
  table, never pooled with the simulated matrix. None is labelled with a known
  cause yet, and the table says so.

**Every simulated row says so** (rule 5), and SC-8 is stated with the narrow
claim beside it (D-105), and with the review its fault effects still owe
(D-270).

Reference: docs/DECISIONS.md D-105, D-270, D-272, D-278; ``EVALUATION.md``
§11.2.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from statistics import median

from meridian.datasets.diagnosis_runs import DiagnosisRun
from meridian.datasets.manifest import content_sha256
from meridian.datasets.publish import SnapshotDirectory
from meridian.datasets.row_fields import jsonl_rows
from meridian.datasets.weighting import wilson
from meridian.reliability.diagnosis_evidence import CAUSES
from meridian.reports.diagnosis_truth import CAUSE_OF, TRUTHS, Judged, judge_run

__all__ = [
    "ANSWERS",
    "DIAGNOSIS_FILE",
    "EFFECTS_REVIEWED",
    "RECALL_TARGET",
    "WRONG_TARGET",
    "diagnosis_rows",
]

DIAGNOSIS_FILE = "diagnosis.jsonl"

ANSWERS = (*CAUSES, "undetermined")
"""Every answer a diagnosis can give, in the matrix's column order."""

RECALL_TARGET = 0.80
WRONG_TARGET = 0.05
"""SC-8's proposed targets (``EVALUATION.md`` §1)."""

EFFECTS_REVIEWED = False
"""Whether the fault effects SC-8 is scored against have had D-105's review.

``docs/SCALE-AND-FAULTS.md`` records it; ``tests/unit/test_report_diagnosis.py``
holds this to what that file says. Until it is True, SC-8 is reported as
unreviewed and never as met (D-270)."""

Rows = list[dict[str, object]]


def diagnosis_rows(runs: Sequence[DiagnosisRun], raw: SnapshotDirectory) -> Rows:
    """Every row of the section, from the sealed runs and the raw snapshot."""
    judged = {_name(run): judge_run(run) for run in runs}
    pairs = [one for found in judged.values() for one in found]
    rows: Rows = [
        {
            "row": "runs",
            "simulated": True,
            "runs": [
                {"run": name, **_described(run)}
                for name, run in zip(judged, runs, strict=True)
            ],
            "reviewed": EFFECTS_REVIEWED,
        }
    ]
    rows.extend(_matrix(pairs))
    rows.extend(_recalls(pairs))
    rows.extend(_spread(judged))
    rows.append(_fractions(pairs))
    rows.append(_sc8(rows, bool(pairs)))
    rows.extend(_real(raw))
    return rows


def _name(run: DiagnosisRun) -> str:
    return content_sha256(run.directory.manifest).hex()[:12]


def _described(run: DiagnosisRun) -> dict[str, object]:
    keep = ("scenario", "master_seed", "stations", "hours")
    return {key: run.run.get(key) for key in keep}


def _matrix(pairs: Sequence[Judged]) -> Rows:
    counts = Counter((one.truth, one.diagnosed) for one in pairs)
    return [
        {
            "row": "cell",
            "truth": truth,
            "diagnosed": answer,
            "count": counts[truth, answer],
            "simulated": True,
        }
        for truth in TRUTHS
        for answer in ANSWERS
    ]


def _recalls(pairs: Sequence[Judged]) -> Rows:
    rows: Rows = []
    for cause in CAUSES:
        cases = [one for one in pairs if one.truth == cause]
        named = sum(one.diagnosed == cause for one in cases)
        rows.append(
            {
                "row": "recall",
                "cause": cause,
                "cases": len(cases),
                "named": named,
                "recall": (
                    asdict(wilson(named / len(cases), len(cases))) if cases else None
                ),
                "simulated": True,
            }
        )
    return rows


def _spread(judged: Mapping[str, Sequence[Judged]]) -> Rows:
    """Each cause's recall in each run that had a case of it, and their spread."""
    by_cause: defaultdict[str, list[float]] = defaultdict(list)
    for found in judged.values():
        for cause in CAUSES:
            cases = [one for one in found if one.truth == cause]
            if cases:
                by_cause[cause].append(
                    sum(one.diagnosed == cause for one in cases) / len(cases)
                )
    return [
        {
            "row": "spread",
            "cause": cause,
            "runs": len(by_cause[cause]),
            "min": min(by_cause[cause], default=None),
            "median": median(by_cause[cause]) if by_cause[cause] else None,
            "max": max(by_cause[cause], default=None),
            "simulated": True,
        }
        for cause in CAUSES
    ]


def _allowed(one: Judged) -> set[str]:
    """The causes naming which is not wrong for this loss."""
    if one.truth in CAUSES:
        return {one.truth}
    if one.truth in {"acted_not_cause", "several"}:
        return {CAUSE_OF[kind] for kind in one.kinds if kind in CAUSE_OF}
    return set()


def _fractions(pairs: Sequence[Judged]) -> dict[str, object]:
    total = len(pairs)
    wrong = sum(
        one.diagnosed != "undetermined" and one.diagnosed not in _allowed(one)
        for one in pairs
    )
    undetermined = sum(one.diagnosed == "undetermined" for one in pairs)
    return {
        "row": "fractions",
        "diagnoses": total,
        "wrong": wrong,
        "wrong_fraction": wrong / total if total else None,
        "undetermined": undetermined,
        "undetermined_fraction": undetermined / total if total else None,
        "simulated": True,
    }


def _sc8(rows: Rows, measured: bool) -> dict[str, object]:
    """SC-8 against its proposed targets, and whether it may be claimed."""
    if not measured:
        return {"row": "sc8", "status": "not_measured", "simulated": True}
    recalls = [one for one in rows if one["row"] == "recall"]
    (fractions,) = (one for one in rows if one["row"] == "fractions")
    missing = [str(one["cause"]) for one in recalls if not one["cases"]]
    low = [
        str(one["cause"])
        for one in recalls
        if one["cases"]
        and int(str(one["named"])) / int(str(one["cases"])) < RECALL_TARGET
    ]
    wrong = fractions["wrong_fraction"]
    met = not missing and not low and isinstance(wrong, float) and wrong <= WRONG_TARGET
    return {
        "row": "sc8",
        "status": "measured",
        "recall_target": RECALL_TARGET,
        "wrong_target": WRONG_TARGET,
        "causes_without_cases": missing,
        "causes_below_target": low,
        "met": met,
        "claimable": met and EFFECTS_REVIEWED,
        "simulated": True,
    }


def _real(raw: SnapshotDirectory) -> Rows:
    """The raw snapshot's measured diagnoses, by cause: real cases, kept apart."""
    stored = raw.files.get("loss_diagnoses.jsonl", b"")
    measured = [
        one
        for one in jsonl_rows(stored, "loss_diagnoses.jsonl")
        if one.get("simulated") is False
    ]
    counts = Counter(str(one.get("cause")) for one in measured)
    return [
        {
            "row": "real",
            "cause": answer,
            "diagnosed": counts[answer],
            "labelled": 0,
            "simulated": False,
        }
        for answer in ANSWERS
    ]
