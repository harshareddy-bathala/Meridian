"""The data section, and the rule every section keeps: the report is its files.

Reference: docs/DECISIONS.md D-235; ``EVALUATION.md`` §4 and §5.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from meridian.datasets.publish import SnapshotDirectory, read_directory
from meridian.reports.build import build_run
from meridian.reports.config import parse_report_config
from meridian.reports.render import render_report

CREATED = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


@pytest.fixture
def built(raw_snapshot: Any, archive_world: Any, datasets_root: Path) -> Any:
    raw = read_directory(raw_snapshot(archive_world))
    return build_run(
        raw,
        parse_report_config(b""),
        seed=4471,
        root=datasets_root,
        created_at=CREATED,
    )


def rows(data: bytes) -> list[dict[str, Any]]:
    return [json.loads(line) for line in data.splitlines()]


def of(found: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [one for one in found if one["row"] == kind]


def test_the_report_is_rendered_from_its_results_files_alone(built: Any) -> None:
    """Every number report.md prints is in a hashed file, because it came from one."""
    again = render_report(
        run=rows(built.files["run.jsonl"]), data=rows(built.files["data.jsonl"])
    )

    assert again == built.files["report.md"]


def test_measured_and_simulated_are_counted_apart(built: Any) -> None:
    labels = {one["name"]: one for one in of(rows(built.files["data.jsonl"]), "label")}

    assert labels["successful_reception"] == {
        "row": "label",
        "name": "successful_reception",
        "measured": 1,
        "simulated": 1,
    }
    assert labels["confirmed_miss"]["measured"] == 1


def test_every_label_and_every_exclusion_is_stated_zeros_included(built: Any) -> None:
    found = rows(built.files["data.jsonl"])

    assert len(of(found, "label")) == 8
    assert {one["name"] for one in of(found, "exclusion")} >= {
        "simulated",
        "satellite_silent",
        "satellite_state_indeterminate",
    }


def test_the_indeterminate_share_carries_its_interval_and_its_n(built: Any) -> None:
    silences = {
        one["population"]: one for one in of(rows(built.files["data.jsonl"]), "silence")
    }

    measured = silences["measured"]
    assert measured["confirmed_silences"] == 1
    assert measured["indeterminate_of_silences"]["n"] == 1
    assert measured["indeterminate_of_silences"]["high"] > 0
    assert silences["simulated"]["indeterminate_of_silences"] is None


def test_both_populations_state_their_completeness_and_weighting(built: Any) -> None:
    found = rows(built.files["data.jsonl"])

    for kind in ("completeness", "weighting"):
        assert [one["population"] for one in of(found, kind)] == ["own", "archive"]


def test_the_run_names_what_it_read_by_hash(
    built: Any, raw_snapshot: Any, archive_world: Any
) -> None:
    raw: SnapshotDirectory = read_directory(raw_snapshot(archive_world))
    inputs = of(rows(built.files["data.jsonl"]), "input")

    assert built.manifest.derived_from == bytes.fromhex(inputs[0]["sha256"])
    assert inputs[0]["as_of"].startswith(raw.manifest.as_of.date().isoformat())
    assert built.manifest.parameters["evaluation_dataset"] == bytes.fromhex(
        inputs[1]["sha256"]
    )


def test_when_it_was_built_is_not_part_of_the_run(
    built: Any, raw_snapshot: Any, archive_world: Any, datasets_root: Path
) -> None:
    from meridian.datasets.manifest import content_sha256

    later = build_run(
        read_directory(raw_snapshot(archive_world)),
        parse_report_config(b""),
        seed=4471,
        root=datasets_root,
        created_at=datetime(2027, 1, 1, tzinfo=UTC),
    )

    assert content_sha256(later.manifest) == content_sha256(built.manifest)
