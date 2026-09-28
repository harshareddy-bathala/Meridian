"""Scoring a pass not yet flown, from a published model and a labelled dataset (D-169).

The claim that matters is **no serving skew**: a pass scored live gets exactly
the features its training example had, computed by the same code from the same
history. It is checked on every example of a small world labelled by the
pipeline's own command, with a model written by hand so each feature carries
weight. Then that nothing the station did after a pass reaches its score, with
its positive control, and the refusals.

Reference: docs/DECISIONS.md D-148, D-157, D-161, D-169.
"""

from __future__ import annotations

import random
import shutil
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

import pytest

from meridian.cli import main
from meridian.datasets.canonical import canonical_line
from meridian.datasets.label_rows import read_labels
from meridian.datasets.manifest import Manifest, file_entry
from meridian.datasets.manifest_parse import whole
from meridian.datasets.publish import publish_directory, read_directory
from meridian.prediction.configurations import CONFIGURATIONS, FALLBACK
from meridian.prediction.examples import own_examples
from meridian.prediction.feature_rows import read_feature_rows
from meridian.prediction.live import (
    LivePass,
    LiveScorer,
    LiveScoringError,
    PassGeometry,
    PassTrack,
    load_live_scorer,
    newest_dataset,
)
from meridian.prediction.model_config import ModelConfig
from meridian.prediction.model_files import publish_model, read_model
from meridian.prediction.score import GEOMETRY_FALLBACK, predict

SINCE = datetime(2026, 9, 1, tzinfo=UTC)
AS_OF = datetime(2026, 9, 23, 6, 0, tzinfo=UTC)
DAYS = 21
SATELLITE = "norad:57166"
STATIONS = ("st_a", "st_b")
LIVE_AT = SINCE + timedelta(days=12, hours=3)
"""A pass scored as if not yet flown, with the dataset's later days on disk."""

Flip = Callable[[datetime], bool]


class Label(Protocol):
    """Label ``world(flip)`` under a root named ``name``; the root and the dataset."""

    def __call__(self, name: str, flip: Flip = ...) -> tuple[Path, Path]: ...


def never(_aos: datetime) -> bool:
    return False


def world(flip: Flip = never) -> Mapping[str, Sequence[Mapping[str, object]]]:
    """Three weeks of one satellite over two stations, with every environment
    feature fed: detections placed on tracks, noise floors, and every third
    rise predicted twice."""
    rng = random.Random(169)
    rows: dict[str, list[Mapping[str, object]]] = {
        name: [] for name in ("passes", "assignments", "observations", "pass_tracks")
    }
    pass_id = 0
    for day in range(DAYS):
        for slot in range(4):
            for offset, station in enumerate(STATIONS):
                pass_id += 1
                aos = SINCE + timedelta(days=day, hours=6 * slot + offset + 1)
                elevation = rng.uniform(5.0, 85.0)
                decoded = rng.random() < 1 / (1 + 2.718 ** (-(elevation - 35) / 10))
                predicted = _pass(pass_id, station, aos, elevation, rng)
                rows["passes"].append(predicted)
                rows["pass_tracks"].append(_track(pass_id, aos, predicted, elevation))
                _report(rows, predicted, decoded != flip(aos), rng)
                if pass_id % 3 == 0:
                    rows["passes"].append(
                        predicted
                        | {
                            "id": 10_000 + pass_id,
                            "aos": aos + timedelta(seconds=25),
                            "computed_at": aos - timedelta(hours=2),
                        }
                    )
    return rows | {
        "element_sets": [
            {"id": day, "satellite_id": SATELLITE, "epoch": SINCE + timedelta(days=day)}
            for day in range(DAYS)
        ],
        "stations": [{"station_id": one, "lon_deg": 77.6} for one in STATIONS],
        "transmitters": [
            {
                "id": 1,
                "satellite_id": SATELLITE,
                "centre_freq_hz": 137_900_000,
                "active": True,
                "deleted_at": None,
            }
        ],
    }


def _pass(
    pass_id: int, station: str, aos: datetime, elevation: float, rng: random.Random
) -> dict[str, object]:
    return {
        "id": pass_id,
        "satellite_id": SATELLITE,
        "station_id": station,
        "aos": aos,
        "los": aos + timedelta(minutes=12),
        "max_elevation_deg": elevation,
        "aos_azimuth_deg": rng.uniform(0.0, 360.0),
        "los_azimuth_deg": rng.uniform(0.0, 360.0),
        "element_set_id": (aos - SINCE).days,
        "computed_at": aos - timedelta(hours=5),
        "simulated": False,
    }


def _track(
    pass_id: int, aos: datetime, predicted: Mapping[str, object], elevation: float
) -> dict[str, object]:
    """24 samples, azimuth swept rise to set, elevation up to the peak and down."""
    rise, fall = (
        float(str(predicted["aos_azimuth_deg"])),
        float(str(predicted["los_azimuth_deg"])),
    )
    samples = 24
    return {
        "pass_id": pass_id,
        "start": aos,
        "step_s": 30,
        "azimuth_deg": [
            round((rise + (fall - rise) * n / samples) % 360.0, 2)
            for n in range(samples)
        ],
        "elevation_deg": [
            round(elevation * (1 - abs(n - samples / 2) / (samples / 2)), 2)
            for n in range(samples)
        ],
    }


def _report(
    rows: dict[str, list[Mapping[str, object]]],
    predicted: Mapping[str, object],
    decoded: bool,
    rng: random.Random,
) -> None:
    number, aos = predicted["id"], predicted["aos"]
    assert isinstance(aos, datetime)
    rows["assignments"].append(
        {
            "assignment_id": f"as_{number}",
            "pass_id": number,
            "station_id": predicted["station_id"],
            "start_at": aos,
            "end_at": predicted["los"],
            "decision": "scheduled",
            "state": "reported",
            "model_config": "A",
            "simulated": False,
        }
    )
    rows["observations"].append(
        {
            "assignment_id": f"as_{number}",
            "revision": 1,
            "outcome": "decoded" if decoded else "signal_no_decode",
            "first_detection_at": aos + timedelta(seconds=rng.uniform(20, 120))
            if decoded
            else None,
            "noise_floor_dbfs": rng.uniform(-105.0, -90.0),
            "simulated": False,
        }
    )


# --- a model written by hand, every feature weighted --------------------------


def linear(features: tuple[str, ...]) -> dict[str, object]:
    size = len(features)
    return {
        "features": list(features),
        "mean": [0.0] * size,
        "scale": [50.0] * size,
        "coefficients": [(-1) ** n * (n + 1) / size for n in range(size)],
        "intercept": 0.1,
        "calibration": {"a": 0.9, "b": -0.05},
    }


@dataclass(frozen=True)
class HandWritten:
    """What ``publish_model`` takes from a fit."""

    document: Mapping[str, object]
    counts: Mapping[str, int]


def a_model(root: Path, dataset: Path, name: str, *, history: int = 5) -> Path:
    configuration = CONFIGURATIONS[name]
    document = {
        "model_format": 1,
        "configuration": name,
        "reads_history": configuration.reads_history,
        "min_station_history": history,
        "configured": linear(configuration.features),
        "fallback": linear(FALLBACK.features) if configuration.reads_history else None,
    }
    return publish_model(
        HandWritten(document, {"examples": 1}),
        dataset=read_directory(dataset),
        config=ModelConfig(configuration=name, min_station_history=history),
        root=root,
        created_at=AS_OF,
    ).path


# --- publishing and labelling a world under a root ----------------------------


@pytest.fixture
def labelled(
    raw_snapshot: Callable[[Mapping[str, Sequence[Mapping[str, object]]]], Path],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> Iterator[Label]:
    """Label ``world(flip)`` under a root of its own; the root and the dataset."""

    def label(name: str, flip: Flip = never) -> tuple[Path, Path]:
        raw = raw_snapshot(world(flip))
        root = tmp_path / name
        held = root / "snapshots" / raw.name
        shutil.copytree(raw, held)
        capsys.readouterr()
        assert main(["snapshot", "--root", str(root), "label", str(held)]) == 0
        first = capsys.readouterr().out.splitlines()[0]
        return root, Path(first.split(": ", 1)[1].rsplit(" (", 1)[0])

    yield label
    unsealed(tmp_path)


def live_pass(
    pass_ids: tuple[int, ...], *, station: str = "st_a", at: datetime = LIVE_AT
) -> LivePass:
    return LivePass(
        pass_id=pass_ids[0],
        pass_ids=pass_ids,
        station_id=station,
        satellite_id=SATELLITE,
        aos=at,
        los=at + timedelta(minutes=12),
        simulated=False,
    )


def geometry_at(at: datetime, *, elevation: float = 50.0) -> PassGeometry:
    return PassGeometry(
        aos=at,
        computed_at=at - timedelta(hours=4),
        max_elevation_deg=elevation,
        aos_azimuth_deg=20.0,
        los_azimuth_deg=170.0,
        element_set_epoch=at - timedelta(hours=30),
        track=PassTrack(
            start=at,
            step_s=30,
            azimuth_deg=tuple(20.0 + 6.25 * n for n in range(24)),
            elevation_deg=tuple(
                round(elevation * (1 - abs(n - 12) / 12), 2) for n in range(24)
            ),
        ),
    )


# --- no serving skew ----------------------------------------------------------


def test_every_training_example_scored_live_has_its_training_features(
    labelled: Label,
) -> None:
    root, dataset_path = labelled("world")
    scorer = load_live_scorer(a_model(root, dataset_path, "D"), root=root)
    dataset = read_directory(dataset_path)
    raw = read_directory(next((root / "snapshots").iterdir()))
    rows = read_feature_rows(raw.files)
    passes = read_labels(dataset.files)
    settle = whole(dataset.manifest.parameters["settle_margin_s"], "settle")
    examples = own_examples(passes, rows, settle_margin_s=settle).examples
    by_key = {(one.station_id, one.aos): one for one in passes}

    live = [
        LivePass(
            pass_id=held.pass_id,
            pass_ids=held.pass_ids,
            station_id=held.station_id,
            satellite_id=held.satellite_id,
            aos=held.aos,
            los=held.los,
            simulated=held.simulated,
        )
        for held in (by_key[(one.station_id, one.aos)] for one in examples)
    ]
    geometry = {
        member: rows.geometry[member] for one in live for member in one.pass_ids
    }
    scored = scorer.score(live, geometry)

    assert len(examples) > 100
    assert any(len(one.pass_ids) > 1 for one in live)
    for example, one in zip(examples, live, strict=True):
        found = scored[one.pass_id]
        assert found.features == example.features
        assert found.station_history == example.station_history
        assert found.prediction == predict(
            scorer.model, example.features, example.station_history
        )
    paths = {found.prediction.path for found in scored.values()}
    assert paths == {"configured", GEOMETRY_FALLBACK}
    assert any(value != 0.0 for value in examples[-1].features.values())


def test_the_history_scored_from_is_stated(
    labelled: Label,
) -> None:
    root, dataset_path = labelled("world")
    model = a_model(root, dataset_path, "C")
    scorer = load_live_scorer(model, root=root)

    assert scorer.past is not None
    assert scorer.past.as_of == AS_OF
    assert scorer.past.dataset_sha256 == read_directory(model).manifest.derived_from


# --- only the past reaches a score --------------------------------------------


def score_at_live_time(labelled: Label, name: str, flip: Flip) -> float:
    root, dataset_path = labelled(name, flip)
    scorer = load_live_scorer(a_model(root, dataset_path, "D"), root=root)
    one = live_pass((90_001,))
    return scorer.score([one], {90_001: geometry_at(LIVE_AT)})[
        90_001
    ].prediction.probability


def test_nothing_the_station_did_after_the_pass_reaches_its_score(
    labelled: Label,
) -> None:
    """Every outcome from the pass's rise on reversed in the raw snapshot."""
    original = score_at_live_time(labelled, "original", never)
    reversed_later = score_at_live_time(labelled, "later", lambda aos: aos >= LIVE_AT)

    assert reversed_later == original


def test_what_it_did_before_the_pass_does(
    labelled: Label,
) -> None:
    """Positive control: the comparison above can fail."""
    original = score_at_live_time(labelled, "original", never)
    reversed_earlier = score_at_live_time(
        labelled, "earlier", lambda aos: aos < LIVE_AT - timedelta(days=2)
    )

    assert reversed_earlier != original


# --- a pass the snapshot has never seen ---------------------------------------


def test_a_rise_predicted_twice_live_diverges_by_their_spread(
    labelled: Label,
) -> None:
    root, dataset_path = labelled("world")
    scorer = load_live_scorer(a_model(root, dataset_path, "D"), root=root)
    later = LIVE_AT + timedelta(seconds=40)

    scored = scorer.score(
        [live_pass((90_001, 90_002))],
        {90_001: geometry_at(LIVE_AT), 90_002: geometry_at(later)},
    )

    assert scored[90_001].features["element_set_divergence_s"] == 40.0


def test_a_new_station_takes_the_geometry_route(
    labelled: Label,
) -> None:
    root, dataset_path = labelled("world")
    scorer = load_live_scorer(a_model(root, dataset_path, "D"), root=root)

    scored = scorer.score(
        [live_pass((90_001,), station="st_new")], {90_001: geometry_at(LIVE_AT)}
    )

    assert scored[90_001].prediction.path == GEOMETRY_FALLBACK
    assert scored[90_001].prediction.reason == "a new station: no settled outcomes"


def test_a_model_reading_no_history_needs_no_dataset(
    labelled: Label,
) -> None:
    root, dataset_path = labelled("world")
    model = a_model(root, dataset_path, "A")
    removed(root / "evaluation")

    scorer = load_live_scorer(model, root=root)
    scored = scorer.score([live_pass((90_001,))], {90_001: geometry_at(LIVE_AT)})

    assert scorer.past is None
    assert scored[90_001].prediction.path == "configured"
    assert 0.0 < scored[90_001].prediction.probability < 1.0


def removed(path: Path) -> None:
    """Delete a published, read-only directory."""
    unsealed(path)
    shutil.rmtree(path)


def unsealed(under: Path) -> None:
    for path in sorted(under.rglob("*"), reverse=True):
        path.chmod(0o700 if path.is_dir() else 0o600)
    under.chmod(0o700)


# --- refusals -----------------------------------------------------------------


def test_a_model_reading_history_with_no_dataset_is_refused(
    labelled: Label,
) -> None:
    root, dataset_path = labelled("world")
    model = a_model(root, dataset_path, "D")
    removed(root / "evaluation")

    with pytest.raises(LiveScoringError, match="holds no labelled dataset"):
        load_live_scorer(model, root=root)


def test_a_dataset_whose_raw_snapshot_is_gone_is_refused(
    labelled: Label,
) -> None:
    root, dataset_path = labelled("world")
    model = a_model(root, dataset_path, "D")
    removed(root / "snapshots")

    with pytest.raises(LiveScoringError, match="cannot be read as history"):
        load_live_scorer(model, root=root)


def test_a_scorer_for_a_history_model_is_refused_without_the_past(
    labelled: Label,
) -> None:
    root, dataset_path = labelled("world")
    model = read_model(a_model(root, dataset_path, "D")).model

    with pytest.raises(LiveScoringError, match="reads history, and no labelled"):
        LiveScorer(model, b"sha")


def test_a_pass_outside_its_rise_or_without_geometry_is_refused(
    labelled: Label,
) -> None:
    root, dataset_path = labelled("world")
    scorer = load_live_scorer(a_model(root, dataset_path, "A"), root=root)
    stray = LivePass(
        pass_id=1,
        pass_ids=(2,),
        station_id="st_a",
        satellite_id=SATELLITE,
        aos=LIVE_AT,
        los=LIVE_AT,
        simulated=False,
    )

    with pytest.raises(LiveScoringError, match="not among its rise"):
        scorer.score([stray], {1: geometry_at(LIVE_AT), 2: geometry_at(LIVE_AT)})
    with pytest.raises(LiveScoringError, match="no geometry for"):
        scorer.score([live_pass((90_001, 90_002))], {90_001: geometry_at(LIVE_AT)})


# --- the newest dataset -------------------------------------------------------


def a_dataset(root: Path, name: str, as_of: datetime, created_at: datetime) -> None:
    data = canonical_line({"name": name})
    manifest = Manifest(
        kind="evaluation_dataset",
        schema_revision="0016",
        since=SINCE,
        as_of=as_of,
        files=(file_entry("labels.jsonl", data),),
        created_at=created_at,
        derived_from=bytes(32),
        transformation_version="labels-1",
        config_sha256=bytes(32),
    )
    publish_directory(root / "evaluation", name, manifest, {"labels.jsonl": data})


def test_the_newest_dataset_is_the_latest_as_of_then_the_latest_labelled(
    datasets_root: Path,
) -> None:
    """Names sort against both rules, so neither is met by the listing order."""
    later = AS_OF + timedelta(days=1)
    a_dataset(datasets_root, "z_old", AS_OF, later + timedelta(days=5))
    a_dataset(datasets_root, "b_new_first", later, later)
    a_dataset(datasets_root, "a_new_relabelled", later, later + timedelta(hours=1))

    found = newest_dataset(datasets_root)

    assert found is not None
    assert found.path.name == "a_new_relabelled"


def test_no_dataset_is_none(datasets_root: Path) -> None:
    assert newest_dataset(datasets_root) is None
