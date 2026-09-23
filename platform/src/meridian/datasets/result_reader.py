"""An evaluation dataset's results, read back — at its threshold or another.

Every evaluation dataset carries its selection (D-154), so any result drawn
from one can reach it. :func:`read_results` is how: it rebuilds each
population's :class:`~meridian.datasets.result.EvaluationResult` from the
verified files and manifest.

**Completeness is recomputed; the weights are read.** The summary is rebuilt
from ``station_days.jsonl``, whose digest the manifest holds, so a reader can
ask what a different threshold would have kept without relabelling — the
sensitivity D-151 asks for, at any value. The weights do not depend on the
threshold (every eligible pass is weighted, D-153), so their diagnostics are
read from the manifest as ``label`` wrote them.

Reference: docs/DECISIONS.md D-151, D-153, D-154.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import date

from meridian.datasets.completeness import POPULATIONS, STATUSES, StationDay, summarise
from meridian.datasets.manifest_parse import (
    MalformedManifestError,
    mapping,
    text,
    whole,
)
from meridian.datasets.publish import SnapshotDirectory
from meridian.datasets.result import EvaluationResult, NotWeighted, Weighting
from meridian.datasets.selection_config import parse_completeness
from meridian.datasets.weighting import IpwDiagnostics, Rate

__all__ = [
    "NoSelectionError",
    "read_results",
]

_STATION_DAYS = "station_days.jsonl"
_RATED = frozenset(("retained", "below_threshold"))


class NoSelectionError(ValueError):
    """A directory that carries no selection to read.

    A raw snapshot, which is not labelled yet, or a dataset labelled before
    Stage 16, which is to be labelled again.
    """


def read_results(
    dataset: SnapshotDirectory, *, threshold: float | None = None
) -> tuple[EvaluationResult, ...]:
    """Each population's result, from a verified evaluation dataset.

    Args:
        dataset: An evaluation dataset, as :func:`read_directory` verified it.
        threshold: A completeness threshold to judge station-days at in place
            of the one the dataset was labelled under.

    Returns:
        One result per population, in :data:`POPULATIONS` order.

    Raises:
        NoSelectionError: The directory carries no selection.
        LabelConfigError: ``threshold`` is outside 0..1.
        MalformedManifestError: The summary or a station-day row is not the
            shape ``label`` writes.
    """
    manifest = dataset.manifest
    if manifest.kind != "evaluation_dataset":
        message = f"{dataset.path} is a raw snapshot; label it first"
        raise NoSelectionError(message)
    if not manifest.summary:
        message = (
            f"{dataset.path} was labelled before Stage 16 and carries no "
            "completeness; label its raw snapshot again"
        )
        raise NoSelectionError(message)
    config = parse_completeness(manifest.parameters.get("completeness", {}))
    if threshold is not None:
        config = replace(config, threshold=threshold)
    days = tuple(
        _day(json.loads(line), config.threshold)
        for line in dataset.files[_STATION_DAYS].splitlines()
    )
    populations = mapping(manifest.summary.get("populations"), "summary.populations")
    return tuple(
        EvaluationResult(
            population=population,
            completeness=summarise(days, population, config),
            weighting=_weighting(
                mapping(
                    mapping(populations.get(population), population).get("weighting"),
                    f"{population}.weighting",
                )
            ),
        )
        for population in POPULATIONS
    )


def _day(row: object, threshold: float) -> StationDay:
    """A station-day as written, its status judged again at ``threshold``."""
    held = mapping(row, "station day")
    status = text(held.get("status"), "station day status")
    if status not in STATUSES:
        message = f"station day status {status!r} is not one of {STATUSES}"
        raise MalformedManifestError(message)
    eligible = whole(held.get("eligible"), "station day eligible")
    attempted = whole(held.get("attempted"), "station day attempted")
    if status in _RATED:
        # The same division ``completeness`` judges by, so 7/10 at 0.7 agrees.
        status = "retained" if attempted / eligible >= threshold else "below_threshold"
    return StationDay(
        population=text(held.get("population"), "station day population"),
        station=text(held.get("station"), "station day station"),
        day=date.fromisoformat(text(held.get("day"), "station day day")),
        eligible=eligible,
        attempted=attempted,
        usable=whole(held.get("usable"), "station day usable"),
        status=status,
    )


def _weighting(held: Mapping[str, object]) -> Weighting:
    """The diagnostics as :meth:`IpwDiagnostics.parameters` wrote them."""
    if "not_weighted" in held:
        return NotWeighted(text(held["not_weighted"], "not_weighted"))
    overlap = mapping(held.get("overlap"), "overlap")
    return IpwDiagnostics(
        model=text(held.get("model"), "model"),
        floor=_real(held.get("floor"), "floor"),
        available=whole(held.get("available"), "available"),
        weighted=whole(held.get("weighted"), "weighted"),
        unsupported=whole(held.get("unsupported"), "unsupported"),
        certain=whole(held.get("certain"), "certain"),
        floored=whole(held.get("floored"), "floored"),
        unweighted=_rate(held.get("unweighted"), "unweighted"),
        weighted_rate=_rate(held.get("weighted_rate"), "weighted_rate"),
        ess=_real(held.get("ess"), "ess"),
        unreliable=_flag(held.get("unreliable"), "unreliable"),
        weight_quartiles=tuple(
            _real(one, "weight_quartiles") for one in _list(held, "weight_quartiles")
        ),
        overlap_attempted=tuple(
            whole(one, "overlap") for one in _list(overlap, "attempted")
        ),
        overlap_not_attempted=tuple(
            whole(one, "overlap") for one in _list(overlap, "not_attempted")
        ),
    )


def _rate(value: object, what: str) -> Rate | None:
    if value is None:
        return None
    held = mapping(value, what)
    return Rate(
        estimate=_real(held.get("estimate"), f"{what}.estimate"),
        low=_real(held.get("low"), f"{what}.low"),
        high=_real(held.get("high"), f"{what}.high"),
        n=_real(held.get("n"), f"{what}.n"),
    )


def _real(value: object, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        message = f"{what} is not a number"
        raise MalformedManifestError(message)
    return float(value)


def _flag(value: object, what: str) -> bool:
    if not isinstance(value, bool):
        message = f"{what} is not true or false"
        raise MalformedManifestError(message)
    return value


def _list(held: Mapping[str, object], what: str) -> list[object]:
    value = held.get(what)
    if not isinstance(value, list):
        message = f"{what} is not a list"
        raise MalformedManifestError(message)
    return value
