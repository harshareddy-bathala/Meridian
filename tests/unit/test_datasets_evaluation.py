"""``meridian.datasets.evaluation`` — a raw snapshot and a configuration, labelled.

No database: the raw snapshot is published by hand through the same writer the
export uses (``tests/unit/conftest.py``). What is asserted is what the dataset
says it came from, and that the same inputs make the same directory.

Reference: docs/DECISIONS.md D-139, D-143, D-144.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from meridian.datasets.evaluation import (
    NotARawSnapshotError,
    build_evaluation_dataset,
)
from meridian.datasets.label_config import LabelConfig, config_sha256
from meridian.datasets.labels import TRANSFORMATION_VERSION
from meridian.datasets.manifest import content_sha256
from meridian.datasets.publish import read_directory
from meridian.datasets.snapshot_rows import MalformedSnapshotError

CREATED = datetime(2026, 9, 23, 7, 0, tzinfo=UTC)


def build(raw: Path, root: Path, config: LabelConfig | None = None) -> Any:
    return build_evaluation_dataset(
        read_directory(raw), config or LabelConfig(), root=root, created_at=CREATED
    )


def lines(path: Path, name: str) -> list[dict[str, Any]]:
    return [json.loads(one) for one in (path / name).read_bytes().splitlines()]


def test_every_pass_is_labelled_in_the_dataset(
    raw_snapshot: Any, datasets_root: Path, world: Any
) -> None:
    published = build(raw_snapshot(world), datasets_root)

    labelled = {one["pass_id"]: one for one in lines(published.path, "labels.jsonl")}

    assert labelled[1]["label"] == "successful_reception"
    assert labelled[2]["label"] == "confirmed_miss"
    assert labelled[3]["exclusion_reason"] == "simulated"


def test_archive_receptions_keep_their_vocabulary_and_their_terms(
    raw_snapshot: Any, datasets_root: Path, world: Any
) -> None:
    published = build(raw_snapshot(world), datasets_root)

    (row,) = lines(published.path, "archive_receptions.jsonl")

    assert row["archive_outcome"] == "no_data"
    assert row["licence"] == "CC-BY-4.0"
    assert "label" not in row


def test_the_dataset_names_exactly_what_it_came_from(
    raw_snapshot: Any, datasets_root: Path, world: Any
) -> None:
    raw = raw_snapshot(world)
    published = build(raw, datasets_root)
    manifest = published.manifest

    assert manifest.kind == "evaluation_dataset"
    assert manifest.derived_from == content_sha256(read_directory(raw).manifest)
    assert manifest.transformation_version == TRANSFORMATION_VERSION
    assert manifest.config_sha256 == config_sha256(LabelConfig())
    assert manifest.parameters == LabelConfig().parameters()
    assert manifest.as_of == read_directory(raw).manifest.as_of
    assert published.path.parent == datasets_root / "evaluation"


def test_the_same_inputs_make_the_same_directory(
    raw_snapshot: Any, datasets_root: Path, world: Any
) -> None:
    """The gate, in miniature: labelled twice, one directory, written once."""
    raw = raw_snapshot(world)

    first = build(raw, datasets_root)
    second = build(raw, datasets_root)

    assert second.path == first.path
    assert (first.written, second.written) == (True, False)


def test_a_different_configuration_makes_a_different_dataset(
    raw_snapshot: Any, datasets_root: Path, world: Any
) -> None:
    raw = raw_snapshot(world)

    default = build(raw, datasets_root)
    lenient = build(raw, datasets_root, LabelConfig(silent_min_attempts=1))

    assert lenient.path != default.path


def test_counts_keep_the_two_populations_apart(
    raw_snapshot: Any, datasets_root: Path, world: Any
) -> None:
    counts = build(raw_snapshot(world), datasets_root).manifest.counts

    assert counts["labels.successful_reception.measured"] == 1
    assert counts["labels.successful_reception.simulated"] == 1
    assert counts["excluded.simulated.simulated"] == 1


def test_an_evaluation_dataset_cannot_be_labelled_again(
    raw_snapshot: Any, datasets_root: Path, world: Any
) -> None:
    published = build(raw_snapshot(world), datasets_root)

    with pytest.raises(NotARawSnapshotError, match="not a raw snapshot"):
        build(published.path, datasets_root)


def test_a_row_the_labeller_cannot_read_stops_the_run(
    raw_snapshot: Any, datasets_root: Path, world: Any
) -> None:
    broken = dict(world) | {"passes": [{"id": 1}]}

    with pytest.raises(MalformedSnapshotError):
        build(raw_snapshot(broken), datasets_root)
