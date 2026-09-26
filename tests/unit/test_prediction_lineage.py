"""From a directory to what it was made from, and to the examples it holds.

``fit`` and ``evaluate`` rebuild examples from a dataset and its raw snapshot
rather than storing them (D-157), so the rebuild must be the one the example
builders give, for either population, and a directory must be refused when
its hash is not the one it is named by.

Reference: docs/DECISIONS.md D-144, D-156, D-157, D-163.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from meridian.datasets.canonical import canonical_line
from meridian.datasets.evaluation import build_evaluation_dataset
from meridian.datasets.label_config import LabelConfig
from meridian.datasets.label_rows import read_labels
from meridian.datasets.publish import SnapshotDirectory, read_directory
from meridian.datasets.selection_config import CompletenessConfig
from meridian.datasets.snapshot_rows import parse_rows
from meridian.prediction.examples import (
    ExampleSet,
    archive_examples,
    own_examples,
    weighted,
)
from meridian.prediction.feature_rows import read_bands, read_feature_rows
from meridian.prediction.fit import FittedModel
from meridian.prediction.lineage import (
    LineageError,
    config_of,
    dataset_of,
    examples_of,
    raw_of,
)
from meridian.prediction.model_config import ModelConfig
from meridian.prediction.model_files import publish_model
from meridian.prediction.score import MODEL_FORMAT
from meridian.prediction.splits import Split

CREATED = datetime(2026, 9, 26, tzinfo=UTC)
SINCE = datetime(2026, 9, 1, tzinfo=UTC)


def labelled(
    raw: Path, root: Path, config: LabelConfig | None = None
) -> tuple[SnapshotDirectory, SnapshotDirectory]:
    snapshot = read_directory(raw)
    published = build_evaluation_dataset(
        snapshot, config or LabelConfig(), root=root, created_at=CREATED
    )
    return read_directory(published.path), snapshot


def test_our_examples_are_the_builders_own(
    raw_snapshot: Any, world: Any, datasets_root: Path
) -> None:
    dataset, raw = labelled(raw_snapshot(world), datasets_root)

    inputs = examples_of(dataset, raw, ModelConfig())

    expected = own_examples(
        read_labels(dataset.files),
        read_feature_rows(raw.files),
        settle_margin_s=LabelConfig().settle_margin_s,
    )
    assert inputs.examples == expected
    assert inputs.bands == read_feature_rows(raw.files).bands


def test_the_archive_examples_use_the_datasets_tolerance(
    raw_snapshot: Any,
    archive_world: Any,
    datasets_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tolerance is the one the dataset was labelled with, not a default."""
    config = LabelConfig(completeness=CompletenessConfig(archive_match_tolerance_s=45))
    dataset, raw = labelled(raw_snapshot(archive_world), datasets_root, config)
    handed: list[int] = []

    def recording(rows: Any, *, tolerance_s: int, since: datetime) -> Any:
        handed.append(tolerance_s)
        return archive_examples(rows, tolerance_s=tolerance_s, since=since)

    monkeypatch.setattr("meridian.prediction.lineage.archive_examples", recording)
    inputs = examples_of(
        dataset, raw, ModelConfig(configuration="A", population="archive")
    )

    assert handed == [45]
    assert inputs.examples == archive_examples(
        parse_rows(raw.files), tolerance_s=45, since=SINCE
    )
    assert inputs.examples.examples


def test_a_weighted_configuration_takes_the_datasets_propensities(
    raw_snapshot: Any,
    world: Any,
    datasets_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ipw`` joins ``propensities.jsonl``; ``none`` never reads it."""
    dataset, raw = labelled(raw_snapshot(world), datasets_root)
    handed: list[bytes] = []

    def recording(found: ExampleSet, propensities: bytes) -> ExampleSet:
        handed.append(propensities)
        return weighted(found, propensities)

    monkeypatch.setattr("meridian.prediction.lineage.weighted", recording)
    plain = examples_of(dataset, raw, ModelConfig()).examples
    assert handed == []
    ipw = examples_of(dataset, raw, ModelConfig(weighting="ipw")).examples

    assert handed == [dataset.files["propensities.jsonl"]]
    assert len(ipw.examples) + ipw.without_weight == len(plain.examples)


def test_the_raw_snapshot_is_found_under_the_root_by_its_name(
    raw_snapshot: Any, world: Any, datasets_root: Path
) -> None:
    dataset, raw = labelled(raw_snapshot(world), datasets_root)

    found = raw_of(dataset, root=datasets_root)

    assert found.path == raw.path


def test_a_raw_snapshot_has_no_raw_snapshot(
    raw_snapshot: Any, world: Any, datasets_root: Path
) -> None:
    raw = read_directory(raw_snapshot(world))

    with pytest.raises(LineageError, match="not a dataset"):
        raw_of(raw, root=datasets_root)


def published_model(
    dataset: SnapshotDirectory, root: Path, config: ModelConfig
) -> SnapshotDirectory:
    """A model directory, published from a document written by hand."""
    split = Split(
        train_until=datetime(2026, 9, 10, tzinfo=UTC),
        validate_until=datetime(2026, 9, 15, tzinfo=UTC),
        as_of=dataset.manifest.as_of,
        train=(),
        validate=(),
        test=(),
    )
    linear = {
        "features": ["max_elevation_deg"],
        "mean": [40.0],
        "scale": [20.0],
        "coefficients": [1.5],
        "intercept": 0.1,
        "calibration": {"method": "platt", "a": 1.0, "b": 0.0},
    }
    document: dict[str, object] = {
        "model_format": MODEL_FORMAT,
        "configuration": "A",
        "population": "own",
        "reads_history": False,
        "min_station_history": config.min_station_history,
        "configured": linear,
        "fallback": None,
    }
    fitted = FittedModel(document=document, counts={}, split=split)
    published = publish_model(
        fitted, dataset=dataset, config=config, root=root, created_at=CREATED
    )
    return read_directory(published.path)


def test_a_model_leads_back_to_its_dataset_and_configuration(
    raw_snapshot: Any, world: Any, datasets_root: Path
) -> None:
    dataset, _ = labelled(raw_snapshot(world), datasets_root)
    config = ModelConfig(configuration="A", folds=2, seed=5)
    model = published_model(dataset, datasets_root, config)

    assert dataset_of(model, root=datasets_root).path == dataset.path
    assert config_of(model) == config


def test_settings_that_do_not_hash_to_the_recorded_hash_are_refused(
    raw_snapshot: Any, world: Any, datasets_root: Path
) -> None:
    dataset, _ = labelled(raw_snapshot(world), datasets_root)
    model = published_model(dataset, datasets_root, ModelConfig(configuration="A"))
    edited = replace(
        model,
        manifest=replace(
            model.manifest, parameters={**model.manifest.parameters, "seed": 9}
        ),
    )

    with pytest.raises(LineageError, match="do not hash to its config_sha256"):
        config_of(edited)


def test_an_ipw_fit_on_a_dataset_without_weights_is_refused(
    raw_snapshot: Any, world: Any, datasets_root: Path
) -> None:
    """A dataset labelled before Stage 16 has no weights to join."""
    dataset, raw = labelled(raw_snapshot(world), datasets_root)
    older = replace(
        dataset,
        files={k: v for k, v in dataset.files.items() if k != "propensities.jsonl"},
    )

    with pytest.raises(LineageError, match=r"holds no propensities\.jsonl"):
        examples_of(older, raw, ModelConfig(weighting="ipw"))


def test_archive_examples_need_no_pass_track(
    raw_snapshot: Any, archive_world: Any, datasets_root: Path
) -> None:
    """A snapshot exported before Stage 17 has no tracks; archive passes never
    read one, so an archive fit is not refused for the want of it."""
    dataset, raw = labelled(raw_snapshot(archive_world), datasets_root)
    before_tracks = replace(
        raw, files={k: v for k, v in raw.files.items() if k != "pass_tracks.jsonl"}
    )
    config = ModelConfig(configuration="A", population="archive")

    assert examples_of(dataset, before_tracks, config) == examples_of(
        dataset, raw, config
    )


def transmitters(*rows: dict[str, object]) -> dict[str, bytes]:
    return {"transmitters.jsonl": b"".join(canonical_line(row) for row in rows)}


def transmitter(
    number: int, hz: float, *, active: bool = True, deleted: bool = False
) -> dict[str, object]:
    return {
        "id": number,
        "satellite_id": "norad:57166",
        "centre_freq_hz": hz,
        "active": active,
        "deleted_at": datetime(2026, 9, 1, tzinfo=UTC) if deleted else None,
    }


@pytest.mark.parametrize(
    ("retired", "band"),
    [
        (transmitter(1, 437e6, active=False), "vhf"),
        (transmitter(1, 437e6, deleted=True), "vhf"),
        (transmitter(1, 437e6), "uhf"),
    ],
)
def test_a_satellites_band_is_its_live_downlinks(
    retired: dict[str, object], band: str
) -> None:
    """A retired UHF transmitter numbered first does not decide the band."""
    files = transmitters(retired, transmitter(2, 137.9e6))

    assert read_bands(files) == {"norad:57166": band}


def test_a_satellite_with_no_live_transmitter_keeps_its_first() -> None:
    files = transmitters(
        transmitter(2, 137.9e6, active=False), transmitter(1, 437e6, deleted=True)
    )

    assert read_bands(files) == {"norad:57166": "uhf"}


def test_an_archive_model_names_the_sources_it_was_fitted_on(
    raw_snapshot: Any, archive_world: Any, datasets_root: Path
) -> None:
    dataset, _ = labelled(raw_snapshot(archive_world), datasets_root)
    model = published_model(dataset, datasets_root, ModelConfig(configuration="A"))

    assert dataset.manifest.sources
    assert model.manifest.sources == dataset.manifest.sources
