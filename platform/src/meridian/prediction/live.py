"""Scoring the passes a scheduler run is deciding, from a published model — D-169.

The scheduler consumes predictions, never the observation store
(``ARCHITECTURE.md``), so a model's probability for a pass not yet flown is
computed here, from files:

* **the model** — a published directory (D-163), read and verified;
* **the past**, for a model that reads history (C, D) — the newest labelled
  dataset under the datasets root, and the raw snapshot it was labelled from,
  followed through :mod:`meridian.prediction.lineage` and verified. Its
  ``as_of`` is how old the history is, and every prediction states it.

**The features are the ones the model was fitted on, computed the same way.**
A pass to score becomes a :class:`~meridian.datasets.labels.LabelledPass` with
no label, and goes through :func:`~meridian.prediction.features.compute_features`
beside the dataset's own passes, with the history and environment they were
built from. Nothing is recomputed differently for the live path, so there is
no serving skew to explain: a pass in the dataset scored here gets exactly the
features its training example had.

**The geometry is handed in.** Prediction reaches no orbit and no database
(``test_prediction_boundaries``), so the scheduler reads each pass, and each
other prediction of its rise, from ``passes``, computes its track, and passes
them here as :class:`~meridian.prediction.feature_rows.PassGeometry`.

A model that reads no history (A, B) needs no dataset, and is given none.

Reference: docs/DECISIONS.md D-148, D-157, D-161, D-163, D-169.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from meridian.datasets.evaluation import EVALUATION
from meridian.datasets.label_rows import read_labels
from meridian.datasets.labels import LabelledPass
from meridian.datasets.manifest import content_sha256, parse_manifest
from meridian.datasets.manifest_parse import MalformedManifestError, whole
from meridian.datasets.publish import (
    MANIFEST_NAME,
    DamagedSnapshotError,
    SnapshotDirectory,
    read_directory,
)
from meridian.datasets.row_fields import MalformedSnapshotError
from meridian.prediction.feature_rows import (
    FeatureRows,
    PassGeometry,
    PassTrack,
    read_feature_rows,
)
from meridian.prediction.features import compute_features, require_current_features
from meridian.prediction.history import History, events_of
from meridian.prediction.lineage import LineageError, raw_of
from meridian.prediction.model_files import read_model
from meridian.prediction.profiles import Environment
from meridian.prediction.score import (
    MalformedModelError,
    Model,
    Prediction,
    predict,
)

__all__ = [
    "LivePass",
    "LiveScorer",
    "LiveScoringError",
    "PassGeometry",
    "PassTrack",
    "Past",
    "Scored",
    "load_live_scorer",
    "newest_dataset",
    "newest_dataset_path",
]


class LiveScoringError(ValueError):
    """A pass, a model or a history that cannot be scored as given."""


@dataclass(frozen=True, slots=True)
class LivePass:
    """One prediction the scheduler is deciding, as a pass to describe."""

    pass_id: int
    pass_ids: tuple[int, ...]
    """Every prediction of this rise the scheduler holds, this one included
    (D-148). Their spread is the element-set divergence feature."""

    station_id: str
    satellite_id: str
    aos: datetime
    los: datetime
    simulated: bool


@dataclass(frozen=True, slots=True)
class Scored:
    """A pass's probability, its route, and the inputs it was computed from."""

    prediction: Prediction
    features: Mapping[str, float]
    station_history: int
    """Settled, usable outcomes at the station before the pass (D-161)."""


@dataclass(frozen=True, slots=True)
class Past:
    """The history a model reads: which dataset, and how recent."""

    dataset_sha256: bytes
    as_of: datetime


@dataclass(frozen=True, slots=True)
class _Known:
    """What the past says, indexed once for every pass scored."""

    rows: FeatureRows
    history: History
    environment: Environment


_NOTHING = FeatureRows(geometry={}, bands={}, longitudes={}, readings={})


class LiveScorer:
    """A model, and the past it reads, ready to score passes."""

    def __init__(
        self,
        model: Model,
        model_sha256: bytes,
        *,
        known: _Known | None = None,
        past: Past | None = None,
    ) -> None:
        """Hold a model, and for one that reads history, what the past says."""
        if model.reads_history and (known is None or past is None):
            message = (
                f"configuration {model.configuration} reads history, and no"
                " labelled dataset was given to read it from"
            )
            raise LiveScoringError(message)
        self.model = model
        self.model_sha256 = model_sha256
        self.past = past if model.reads_history else None
        self._known = known or _Known(
            rows=_NOTHING,
            history=History(()),
            environment=Environment((), _NOTHING, settle_margin_s=0),
        )

    def score(
        self, passes: Sequence[LivePass], geometry: Mapping[int, PassGeometry]
    ) -> dict[int, Scored]:
        """Each pass's probability, by pass id.

        Args:
            passes: The predictions to score.
            geometry: The geometry of every prediction ``passes`` name, each
                pass's own and its rise's others.

        Raises:
            LiveScoringError: A pass that is not among its own rise, or a
                prediction whose geometry was not given.
        """
        for one in passes:
            if one.pass_id not in one.pass_ids:
                message = f"pass {one.pass_id} is not among its rise's predictions"
                raise LiveScoringError(message)
            missing = [member for member in one.pass_ids if member not in geometry]
            if missing:
                message = f"pass {one.pass_id}'s rise has no geometry for {missing}"
                raise LiveScoringError(message)
        known = self._known
        rows = replace(known.rows, geometry={**known.rows.geometry, **geometry})
        vectors = compute_features(
            [_unlabelled(one) for one in passes],
            rows,
            known.history,
            known.environment.with_predictions(geometry),
        )
        found = {}
        for one, vector in zip(passes, vectors, strict=True):
            features = vector.named()
            station_history = known.history.decode_rate(
                ("station", one.station_id), one.aos
            ).trials
            found[one.pass_id] = Scored(
                prediction=predict(self.model, features, station_history),
                features=features,
                station_history=station_history,
            )
        return found


def _unlabelled(one: LivePass) -> LabelledPass:
    """A pass not yet flown, in the shape a labelled one has, with no label."""
    return LabelledPass(
        pass_id=one.pass_id,
        pass_ids=one.pass_ids,
        station_id=one.station_id,
        satellite_id=one.satellite_id,
        aos=one.aos,
        los=one.los,
        label=None,
        exclusion_reason=None,
        source_outcome=None,
        listening_confirmed=None,
        scheduled_by=(),
        simulated=one.simulated,
    )


def newest_dataset_path(root: Path) -> Path | None:
    """Where the labelled dataset with the latest ``as_of`` is, from manifests alone.

    Two labelled from one raw snapshot share an ``as_of``; the one labelled
    later wins, then the directory name, so the choice never depends on the
    order the file system lists them in. Only manifests are read, so a caller
    can ask every round whether a newer one has appeared.

    Raises:
        LiveScoringError: A dataset's manifest cannot be read. Named, and
            one error, so a round refuses rather than failing on the dataset
            layer's own.
    """
    under = root / EVALUATION
    if not under.is_dir():
        return None
    ranked = []
    for path in sorted(under.iterdir()):
        if not (path / MANIFEST_NAME).is_file():
            continue
        try:
            manifest = parse_manifest((path / MANIFEST_NAME).read_bytes())
        except (MalformedManifestError, OSError) as exc:
            message = f"the manifest of {path} cannot be read: {exc}"
            raise LiveScoringError(message) from exc
        if manifest.kind == "evaluation_dataset":
            ranked.append(((manifest.as_of, manifest.created_at, path.name), path))
    return max(ranked)[1] if ranked else None


def newest_dataset(root: Path) -> SnapshotDirectory | None:
    """The labelled dataset with the latest ``as_of``, read and verified.

    Returns:
        ``None`` when there is none.

    Raises:
        DamagedSnapshotError: The newest is not what its manifest says. An
            older one is not taken instead: a history quietly older than the
            operator thinks is worse than a refusal.
        LiveScoringError: A dataset's manifest cannot be read.
    """
    path = newest_dataset_path(root)
    return None if path is None else read_directory(path)


def load_live_scorer(model_path: Path, *, root: Path) -> LiveScorer:
    """Read a model, and the past it reads if it reads one, ready to score.

    Args:
        model_path: The published model directory.
        root: The datasets root, where the newest labelled dataset and its raw
            snapshot are.

    Raises:
        LiveScoringError: Anything that stops the model scoring, named: the
            model is damaged or cannot be scored, or it reads history and the
            root holds no labelled dataset, a damaged one, or not the raw
            snapshot the newest was labelled from. One error, so the scheduler
            can refuse the run without importing the dataset layer's own.
    """
    try:
        return _load(model_path, root)
    except LiveScoringError:
        raise
    except _UNREADABLE as exc:
        message = f"the model at {model_path} cannot score: {exc}"
        raise LiveScoringError(message) from exc


_UNREADABLE = (
    DamagedSnapshotError,
    LineageError,
    MalformedManifestError,
    MalformedModelError,
    MalformedSnapshotError,
)


def _load(model_path: Path, root: Path) -> LiveScorer:
    fitted = read_model(model_path)
    require_current_features(fitted.model)
    model_sha256 = content_sha256(fitted.directory.manifest)
    if not fitted.model.reads_history:
        return LiveScorer(fitted.model, model_sha256)
    dataset = newest_dataset(root)
    if dataset is None:
        message = (
            f"configuration {fitted.model.configuration} reads history, and"
            f" {root / EVALUATION} holds no labelled dataset; label a snapshot"
        )
        raise LiveScoringError(message)
    try:
        raw = raw_of(dataset, root=root)
    except LineageError as exc:
        message = f"the newest labelled dataset cannot be read as history: {exc}"
        raise LiveScoringError(message) from exc
    settle = whole(dataset.manifest.parameters.get("settle_margin_s"), "settle")
    labelled = read_labels(dataset.files)
    rows = read_feature_rows(raw.files)
    known = _Known(
        rows=rows,
        history=History(events_of(labelled, bands=rows.bands, settle_margin_s=settle)),
        environment=Environment(labelled, rows, settle_margin_s=settle),
    )
    past = Past(
        dataset_sha256=content_sha256(dataset.manifest),
        as_of=dataset.manifest.as_of,
    )
    return LiveScorer(fitted.model, model_sha256, known=known, past=past)
