"""A sealed run's prediction section, summarised for the dashboard to publish.

``meridian report summary <run> --output <file>`` writes ``model-summary-1``:
the run's hash, what the figures are and whose they are, the archive's
completeness, and for configurations A, C and D the Brier score against the
base rate, the skill, their intervals and the reliability diagram's ten bins.
The dashboard's reports view reads the file and nothing else (D-325, D-332),
because the API may not import the report module that sealed it.

**Copied, never recomputed.** Every number is read from the run's own results
files — ``prediction.jsonl``, ``data.jsonl``, ``run.jsonl`` — through
:func:`~meridian.datasets.publish.read_directory`, which refuses a run whose
files no longer match its manifest. A summary is then exactly as true as the
sealed run it names by hash, and ``meridian report verify`` checks that run.

**Only an archive run is summarised.** Its figures are another network's
receptions, and the label says so in the words the site, the poster and the
dashboard must all use (D-053, D-099, D-322). A run of our own stations has
no published label yet, and is refused rather than given one by default.

**A configuration the archive cannot take is said so**, with the report's own
reason and no number at all: never a zero (D-086, D-156). B is A's model
(D-160) and D∖conditions has no place in the schema, so neither is written.

Reference: docs/DECISIONS.md D-086, D-156, D-160, D-235, D-322, D-325.
"""

from __future__ import annotations

import json
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path

from meridian.datasets.manifest import content_sha256
from meridian.datasets.publish import SnapshotDirectory
from meridian.reports.data import DATA_FILE
from meridian.reports.prediction_rows import PREDICTION_FILE

__all__ = [
    "ARCHIVE_LABELS",
    "SCHEMA",
    "SUMMARISED",
    "NotSummarisableError",
    "model_summary",
    "write_summary",
]

SCHEMA = "model-summary-1"

SUMMARISED = ("A", "C", "D")
"""The configurations written, in order. B is A's model (D-160)."""

ARCHIVE_LABELS: Mapping[str, tuple[str, str]] = {
    "satnogs_network": (
        "SatNOGS Network",
        "Measured by the SatNOGS network, not by Meridian. Selection-biased: "
        "the archive holds only passes its stations chose to observe.",
    ),
}
"""Each archive source a summary may describe: its name, and the label every
published copy of its figures carries (D-322)."""

_RUN_FILE = "run.jsonl"
_CONFIG_FILE = "config.toml"

Row = Mapping[str, object]


class NotSummarisableError(ValueError):
    """A run this summary cannot describe honestly, and why."""


def model_summary(run: SnapshotDirectory) -> dict[str, object]:
    """The summary of one sealed evaluation report.

    Args:
        run: The run, read and checked against its manifest.

    Returns:
        The ``model-summary-1`` document.

    Raises:
        NotSummarisableError: The directory is not an evaluation report, its
            population is not the archive, it read no source this module has a
            label for, or no configuration was fitted.
    """
    if run.manifest.kind != "evaluation_report":
        message = f"{run.path} is a {run.manifest.kind}, not an evaluation report"
        raise NotSummarisableError(message)
    population = _population(run)
    if population != "archive":
        message = (
            f"{run.path} fitted the {population!r} population; only an archive "
            "run has a published label (D-322)"
        )
        raise NotSummarisableError(message)
    data = _rows(run, DATA_FILE)
    prediction = _rows(run, PREDICTION_FILE)
    models = {str(one["name"]): one for one in prediction if one.get("row") == "model"}
    fitted = [models[name] for name in SUMMARISED if _fitted(models.get(name))]
    if not fitted:
        message = f"{run.path} fitted no configuration, so there is nothing to publish"
        raise NotSummarisableError(message)
    name, label, source = _source(data, run.path)
    split = fitted[0]
    return {
        "schema": SCHEMA,
        "run_sha256": content_sha256(run.manifest).hex(),
        "method": _method(run),
        "label": label,
        "source": {
            "name": name,
            "licence": source["licence"],
            "terms_url": source["terms_url"],
            "retrieved_from": run.manifest.since.isoformat(),
            "retrieved_until": run.manifest.as_of.isoformat(),
        },
        "simulated": False,
        "completeness_ratio": _completeness(data),
        "split": {
            "train_until": split["train_until"],
            "validate_until": split["validate_until"],
            "as_of": split["as_of"],
        },
        "configurations": [
            _configuration(name, models.get(name), prediction) for name in SUMMARISED
        ],
    }


def write_summary(path: Path, summary: Mapping[str, object]) -> None:
    """Write a summary so a reader never sees half of it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_name(f".{path.name}.partial")
    scratch.write_text(
        json.dumps(summary, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )
    scratch.replace(path)


def _population(run: SnapshotDirectory) -> str:
    """Whose passes the run fitted, from the configuration it kept byte for byte."""
    config = tomllib.loads(run.files[_CONFIG_FILE].decode("utf-8"))
    section = config.get("prediction", {})
    return str(section.get("population", "own"))


def _method(run: SnapshotDirectory) -> str:
    (record,) = (one for one in _rows(run, _RUN_FILE) if one.get("row") == "run")
    return str(record["method"])


def _source(data: Sequence[Row], where: Path) -> tuple[str, str, Row]:
    """The one labelled archive source the run read."""
    labelled = [
        one
        for one in data
        if one.get("row") == "source" and one.get("source_id") in ARCHIVE_LABELS
    ]
    if len(labelled) != 1:
        message = (
            f"{where} read {len(labelled)} archive sources with a published label; "
            "a summary describes exactly one"
        )
        raise NotSummarisableError(message)
    (source,) = labelled
    name, label = ARCHIVE_LABELS[str(source["source_id"])]
    return name, label, source


def _completeness(data: Sequence[Row]) -> float | None:
    """Attempted over eligible archive passes, or None with nothing eligible."""
    for one in data:
        if one.get("row") == "completeness" and one.get("population") == "archive":
            eligible = _whole(one["eligible"])
            return (
                None if eligible == 0 else round(_whole(one["attempted"]) / eligible, 6)
            )
    return None


def _configuration(
    name: str, model: Row | None, prediction: Sequence[Row]
) -> dict[str, object]:
    if model is None or not _fitted(model):
        reason = "not fitted" if model is None else str(model.get("reason"))
        return {"name": name, "computable": False, "reason": reason}
    return {
        "name": name,
        "n_test": model["test"],
        "n_decoded": model["test_decoded"],
        "brier": model["brier"],
        "brier_interval": _pair(model.get("brier_interval")),
        "base_rate": model["base_rate"],
        "base_brier": model["base_brier"],
        "skill": model["skill"],
        "skill_interval": _pair(model.get("skill_interval")),
        "computable": True,
        "bins": [
            _bin(one)
            for one in prediction
            if one.get("row") == "bin" and one.get("model") == name
        ],
    }


def _bin(row: Row) -> dict[str, object]:
    observed = row.get("observed")
    rate = observed if isinstance(observed, Mapping) else None
    return {
        "low": row["low"],
        "high": row["high"],
        "n": row["n"],
        "mean_predicted": row["mean_predicted"],
        "observed": None if rate is None else rate["estimate"],
        "observed_interval": None if rate is None else [rate["low"], rate["high"]],
    }


def _pair(interval: object) -> list[object] | None:
    if not isinstance(interval, Mapping):
        return None
    return [interval["low"], interval["high"]]


def _fitted(model: Row | None) -> bool:
    return model is not None and model.get("status") == "fitted"


def _whole(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        message = f"{value!r} is not a count"
        raise NotSummarisableError(message)
    return value


def _rows(run: SnapshotDirectory, name: str) -> list[Row]:
    return [
        json.loads(line)
        for line in run.files[name].decode("utf-8").splitlines()
        if line.strip()
    ]
