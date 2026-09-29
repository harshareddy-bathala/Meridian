"""A dataset's test span, as the retrospective comparison replays it — D-172.

The comparison schedules the past again: every scheduler is handed the passes a
station could have received on each day of the models' test span, and judged
on what those passes returned. This module reads them from files, so the
scheduler never opens a dataset itself (``test_datasets_boundaries``):

* **the models** — A's, C's and D's, each verified and each fitted on this
  dataset, for the same population and the same split dates. B is scored by
  A's model (D-160), so it names none;
* **the candidates** — every eligible physical pass (D-148, D-149) whose
  ``aos`` falls in the test span, ``validate_until`` to ``as_of``, on a
  station-day whose completeness reaches the threshold (D-151). Eligible is
  the completeness denominator's own rule, so a day's completeness is the
  share of its candidates the historical policy attempted;
* **the predictions** — each candidate scored by each model through the
  features it would have had live, computed as its training example's were;
* **the outcomes** — what each candidate returned: the frames its report
  decoded, 0 for a pass decoded nothing from or a confirmed silence, and
  *unknown* for everything else. A pass nobody attempted has no outcome, and
  none is imputed (rule 7).

**The outcomes are kept apart from everything else.** They are a mapping of
their own, which only the oracle and the tally read: a scheduler handed
candidates and predictions has nothing to learn the outcome from.

Simulated passes are no candidate (D-078) and are counted.

Reference: docs/DECISIONS.md D-078, D-148, D-149, D-151, D-160, D-172;
docs/EVALUATION.md §3, §4.1.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from meridian.datasets.completeness import StationDay, own_eligible, own_station_days
from meridian.datasets.label_rows import read_labels
from meridian.datasets.labels import LabelledPass
from meridian.datasets.manifest import content_sha256
from meridian.datasets.manifest_parse import MalformedManifestError, whole
from meridian.datasets.physical_passes import group_physical_passes
from meridian.datasets.pooled_evidence import pool_evidence
from meridian.datasets.publish import (
    DamagedSnapshotError,
    SnapshotDirectory,
    read_directory,
)
from meridian.datasets.row_fields import MalformedSnapshotError, jsonl_rows, number
from meridian.datasets.row_fields import text as row_text
from meridian.datasets.selection_config import parse_completeness
from meridian.datasets.snapshot_rows import PassRow, SnapshotRows, parse_rows
from meridian.prediction.feature_rows import read_feature_rows
from meridian.prediction.features import compute_features
from meridian.prediction.history import History, events_of
from meridian.prediction.lineage import LineageError, raw_of
from meridian.prediction.model_config import ModelConfigError
from meridian.prediction.profiles import Environment
from meridian.prediction.replay_models import (
    MODELLED,
    DamagedReplayError,
    ReplayError,
    check_models,
)
from meridian.prediction.score import MalformedModelError, Model, Prediction, predict

__all__ = [
    "MODELLED",
    "DamagedReplayError",
    "Outcome",
    "Replay",
    "ReplayDay",
    "ReplayError",
    "ReplayPass",
    "load_replay",
]

NEUTRAL_PRIORITY = 1.0
"""A satellite the snapshot holds no priority for, as the live run takes it."""

_DECODED = "successful_reception"
_NOTHING_DECODED = frozenset(("signal_no_decode", "confirmed_miss"))


@dataclass(frozen=True, slots=True)
class ReplayPass:
    """A physical pass a scheduler may take, as its representative predicted it."""

    pass_id: int
    station_id: str
    satellite_id: str
    aos: datetime
    los: datetime
    max_elevation_deg: float
    element_set_epoch: datetime
    """How old the prediction's element set was decides its timing margin."""

    priority: float


@dataclass(frozen=True, slots=True)
class Outcome:
    """What a pass returned, if anybody knows."""

    frames: int | None
    """Frames decoded, 0 for a pass nothing was decoded from, and ``None``
    where the outcome is unknown."""

    why: str
    """The label, or ``frames_not_reported`` for a decode that gave no count."""


@dataclass(frozen=True, slots=True)
class ReplayDay:
    """One retained station-day of the test span, and its candidates."""

    station_id: str
    day: date
    hours: float
    """The part of the day inside the test span: the station-hours it adds."""

    completeness: float
    pass_ids: tuple[int, ...]
    """In ``aos`` order."""


@dataclass(frozen=True, slots=True)
class Replay:
    """Everything the comparison schedules and judges, from verified files."""

    dataset_sha256: bytes
    raw_sha256: bytes
    models: Mapping[str, bytes]
    """Each modelled configuration's model hash."""

    test_from: datetime
    as_of: datetime
    threshold: float
    days: tuple[ReplayDay, ...]
    """Retained station-days, in station then day order."""

    left_out: Mapping[str, int]
    """Station-days of the test span that are not replayed, by status, and
    the simulated passes of the span."""

    passes: Mapping[int, ReplayPass]
    predictions: Mapping[str, Mapping[int, Prediction]]
    """Each modelled configuration's prediction for every candidate."""

    outcomes: Mapping[int, Outcome]


def load_replay(
    dataset_path: Path,
    models: Mapping[str, Path],
    *,
    root: Path,
    threshold: float | None = None,
) -> Replay:
    """Read a dataset's test span, its models' predictions and its outcomes.

    Args:
        dataset_path: The evaluation dataset.
        models: The model directories of A, C and D.
        root: The datasets root, where the raw snapshot is.
        threshold: A completeness threshold in place of the dataset's own.

    Raises:
        ReplayError: A model is missing, another configuration's, or fitted on
            another dataset, population or dates; or a file cannot be read.
        DamagedReplayError: A directory is not what its manifest says.
    """
    try:
        return _load(dataset_path, models, root, threshold)
    except DamagedSnapshotError as exc:
        raise DamagedReplayError(str(exc)) from exc
    except _UNREADABLE as exc:
        raise ReplayError(str(exc)) from exc


_UNREADABLE = (
    LineageError,
    MalformedManifestError,
    MalformedModelError,
    MalformedSnapshotError,
    ModelConfigError,
    OSError,
)


def _load(
    dataset_path: Path,
    models: Mapping[str, Path],
    root: Path,
    threshold: float | None,
) -> Replay:
    dataset = read_directory(dataset_path)
    raw = raw_of(dataset, root=root)
    held, test_from = check_models(models, content_sha256(dataset.manifest))
    parameters = dataset.manifest.parameters
    settle = whole(parameters.get("settle_margin_s"), "settle_margin_s")
    completeness = parse_completeness(parameters.get("completeness", {}))
    if threshold is not None:
        completeness = replace(completeness, threshold=threshold)
    labelled = read_labels(dataset.files)
    span = (test_from, dataset.manifest.as_of)
    in_span = [one for one in labelled if span[0] <= one.aos < span[1]]
    ratios = {
        (one.station, one.day): one
        for one in own_station_days(labelled, completeness)
        if _day_in(one.day, span)
    }
    retained = {key for key, one in ratios.items() if one.status == "retained"}
    chosen = sorted(
        (
            one
            for one in in_span
            if own_eligible(one) and (one.station_id, one.aos.date()) in retained
        ),
        key=lambda one: (one.station_id, one.aos, one.pass_id),
    )
    snapshot = parse_rows(raw.files)
    left_out = {
        status: sum(1 for one in ratios.values() if one.status == status)
        for status in ("below_threshold", "empty")
    }
    return Replay(
        dataset_sha256=content_sha256(dataset.manifest),
        raw_sha256=content_sha256(raw.manifest),
        models={
            name: content_sha256(one.directory.manifest) for name, one in held.items()
        },
        test_from=span[0],
        as_of=span[1],
        threshold=completeness.threshold,
        days=_days(chosen, ratios, span),
        left_out=left_out | {"simulated": sum(one.simulated for one in in_span)},
        passes=_passes(chosen, snapshot, _priorities(raw)),
        predictions=_predictions(
            {name: one.model for name, one in held.items()},
            chosen,
            labelled,
            (raw, settle),
        ),
        outcomes=_outcomes(chosen, snapshot),
    )


def _day_in(day: date, span: tuple[datetime, datetime]) -> bool:
    return _hours(day, span) > 0


def _hours(day: date, span: tuple[datetime, datetime]) -> float:
    """How much of ``day`` the test span covers, in hours."""
    start = datetime.combine(day, time(), tzinfo=UTC)
    low, high = max(start, span[0]), min(start + timedelta(days=1), span[1])
    return max((high - low).total_seconds(), 0.0) / 3600.0


def _days(
    chosen: Sequence[LabelledPass],
    ratios: Mapping[tuple[str, date], StationDay],
    span: tuple[datetime, datetime],
) -> tuple[ReplayDay, ...]:
    grouped: dict[tuple[str, date], list[int]] = {}
    for one in chosen:
        grouped.setdefault((one.station_id, one.aos.date()), []).append(one.pass_id)
    days = []
    for (station, day), pass_ids in sorted(grouped.items()):
        ratio = ratios[(station, day)].completeness
        days.append(
            ReplayDay(
                station_id=station,
                day=day,
                hours=_hours(day, span),
                completeness=float(ratio or 0.0),
                pass_ids=tuple(pass_ids),
            )
        )
    return tuple(days)


def _priorities(raw: SnapshotDirectory) -> dict[str, float]:
    """Each satellite's priority at export, which is when the snapshot holds it."""
    return {
        row_text(one, "satellite_id"): number(one, "priority")
        for one in jsonl_rows(raw.files.get("satellites.jsonl", b""), "satellites")
        if one.get("priority") is not None
    }


def _passes(
    chosen: Sequence[LabelledPass],
    snapshot: SnapshotRows,
    priorities: Mapping[str, float],
) -> dict[int, ReplayPass]:
    predicted: dict[int, PassRow] = {one.pass_id: one for one in snapshot.passes}
    found = {}
    for one in chosen:
        row = predicted.get(one.pass_id)
        if row is None:
            message = f"labelled pass {one.pass_id} is not in the raw snapshot"
            raise ReplayError(message)
        found[one.pass_id] = ReplayPass(
            pass_id=one.pass_id,
            station_id=one.station_id,
            satellite_id=one.satellite_id,
            aos=one.aos,
            los=one.los,
            max_elevation_deg=row.max_elevation_deg,
            element_set_epoch=row.element_set_epoch,
            priority=priorities.get(one.satellite_id, NEUTRAL_PRIORITY),
        )
    return found


def _predictions(
    models: Mapping[str, Model],
    chosen: Sequence[LabelledPass],
    labelled: Sequence[LabelledPass],
    source: tuple[SnapshotDirectory, int],
) -> dict[str, dict[int, Prediction]]:
    """Every model's prediction for every candidate, from its own-aos features."""
    raw, settle = source
    rows = read_feature_rows(raw.files)
    history = History(events_of(labelled, bands=rows.bands, settle_margin_s=settle))
    environment = Environment(labelled, rows, settle_margin_s=settle)
    vectors = compute_features(chosen, rows, history, environment)
    found: dict[str, dict[int, Prediction]] = {name: {} for name in models}
    for one, vector in zip(chosen, vectors, strict=True):
        features = vector.named()
        station_history = history.decode_rate(
            ("station", one.station_id), one.aos
        ).trials
        for name, model in models.items():
            found[name][one.pass_id] = predict(model, features, station_history)
    return found


def _outcomes(
    chosen: Sequence[LabelledPass], snapshot: SnapshotRows
) -> dict[int, Outcome]:
    """What each candidate returned, from its label and its pooled report."""
    pooled = pool_evidence(group_physical_passes(snapshot.passes), snapshot)
    found = {}
    for one in chosen:
        label = one.label or one.exclusion_reason or "unlabelled"
        report = pooled[one.pass_id].report if one.pass_id in pooled else None
        if one.label in _NOTHING_DECODED:
            found[one.pass_id] = Outcome(frames=0, why=label)
        elif one.label == _DECODED and report is not None:
            frames = report.frames_decoded
            why = label if frames is not None else "frames_not_reported"
            found[one.pass_id] = Outcome(frames=frames, why=why)
        else:
            found[one.pass_id] = Outcome(frames=None, why=label)
    return found
