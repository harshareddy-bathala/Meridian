"""``meridian report criteria``: a sealed run's success criteria, copied out.

A sealed evaluation report is published by hand here, its section rows written
as each section writes them, so every shape the summary reads is pinned to a
row: a measured criterion, one not measured with its section's reason, one the
run was not given anything for, and the caveats that stop a met target being
claimed. The summary must copy, never compute, and pass the reader the API
validates strictly (D-368).

Reference: docs/DECISIONS.md D-086, D-278, D-325, D-368.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from meridian.api.public.models.criteria import PublicCriteria
from meridian.cli import main
from meridian.datasets.canonical import canonical_line
from meridian.datasets.manifest import Manifest, content_sha256, file_entry
from meridian.datasets.publish import publish_directory, read_directory
from meridian.reports.criteria import CRITERIA, SCHEMA, criteria_summary
from meridian.reports.summary import NotSummarisableError

SINCE = datetime(2026, 10, 1, tzinfo=UTC)
AS_OF = datetime(2026, 10, 31, tzinfo=UTC)

RUN = {"row": "run", "method": "report-7", "seed": 4471}
SC1 = {
    "row": "sc1",
    "target": 0.2,
    "status": "measured",
    "relative": 0.243,
    "interval": {"low": 0.11, "high": 0.37},
    "station_days": 58,
    "point_meets": True,
    "interval_above": False,
}
SC2 = {
    "row": "sc2",
    "model": "D",
    "target": 0.25,
    "status": "not measured",
    "reason": "D was not fitted",
}
SC4 = {
    "row": "sc4",
    "target": 0.9,
    "window_days": 7,
    "sc4_window": False,
    "status": "measured",
    "capture": 0.93,
    "interval": {"low": 0.88, "high": 0.96},
    "n": 140,
    "point_meets": True,
    "interval_above": False,
}
SC5 = {
    "row": "sc5",
    "target_s": 90,
    "simulated": True,
    "status": "measured",
    "n": 123,
    "max_s": 68.15,
    "within": 123,
    "all_within": True,
}
RECALL = {
    "row": "recall",
    "cause": "obstruction",
    "cases": 20,
    "named": 18,
    "recall": {"estimate": 0.9, "low": 0.7, "high": 0.97, "n": 20.0},
    "simulated": True,
}
FRACTIONS = {
    "row": "fractions",
    "diagnoses": 100,
    "wrong": 3,
    "wrong_fraction": 0.03,
    "undetermined": 4,
    "undetermined_fraction": 0.04,
    "simulated": True,
}
SC8 = {
    "row": "sc8",
    "status": "measured",
    "recall_target": 0.8,
    "wrong_target": 0.05,
    "causes_without_cases": [],
    "causes_below_target": [],
    "met": True,
    "claimable": False,
    "simulated": True,
}


def publish(root: Path, files: Mapping[str, Sequence[Mapping[str, object]]]) -> Path:
    data = {
        name: b"".join(canonical_line(row) for row in rows)
        for name, rows in files.items()
    }
    manifest = Manifest(
        kind="evaluation_report",
        schema_revision="0032",
        since=SINCE,
        as_of=AS_OF,
        files=tuple(file_entry(name, one) for name, one in sorted(data.items())),
        created_at=AS_OF,
        derived_from=bytes(32),
        transformation_version="report-7",
        config_sha256=bytes(32),
    )
    name = content_sha256(manifest).hex()[:12]
    return publish_directory(root / "reports", name, manifest, data).path


@pytest.fixture
def run(tmp_path: Path) -> Path:
    return publish(
        tmp_path,
        {
            "run.jsonl": [RUN],
            "scheduling.jsonl": [SC1],
            "prediction.jsonl": [SC2],
            "reliability.jsonl": [SC4, SC5],
            "diagnosis.jsonl": [RECALL, FRACTIONS, SC8],
        },
    )


def by_id(summary: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {one["id"]: one for one in summary["criteria"]}


def test_every_criterion_a_run_judges_is_stated_in_order(run: Path) -> None:
    summary = criteria_summary(read_directory(run))
    assert summary["schema"] == SCHEMA
    assert summary["method"] == "report-7"
    assert [one["id"] for one in summary["criteria"]] == list(CRITERIA)


def test_a_measured_criterion_is_its_rows_figures_copied(run: Path) -> None:
    sc1 = by_id(criteria_summary(read_directory(run)))["SC-1"]
    assert sc1["status"] == "measured"
    assert sc1["simulated"] is False
    assert sc1["figures"] == [
        {
            "name": "relative_gain",
            "value": 0.243,
            "low": 0.11,
            "high": 0.37,
            "target": 0.2,
            "direction": "at_least",
            "n": 58,
        }
    ]
    assert (sc1["met"], sc1["interval_clears"], sc1["claimable"]) == (
        True,
        False,
        True,
    )


def test_a_criterion_not_measured_carries_its_reason_and_no_number(
    run: Path,
) -> None:
    criteria = by_id(criteria_summary(read_directory(run)))
    assert criteria["SC-2"]["status"] == "not_measured"
    assert criteria["SC-2"]["reason"] == "D was not fitted"
    for one in ("SC-2", "SC-3", "SC-7", "SC-9"):
        assert criteria[one]["figures"] == []
        assert criteria[one]["met"] is None


def test_a_criterion_the_run_was_given_nothing_for_says_so(run: Path) -> None:
    criteria = by_id(criteria_summary(read_directory(run)))
    assert criteria["SC-9"]["status"] == "not_run"
    assert criteria["SC-9"]["reason"] == "the run was given no health-watch run"
    assert criteria["SC-3"]["status"] == "not_run"


def test_simulated_criteria_say_so(run: Path) -> None:
    criteria = by_id(criteria_summary(read_directory(run)))
    assert {one for one, c in criteria.items() if c["simulated"]} == {
        "SC-5",
        "SC-8",
        "SC-9",
    }
    assert criteria["SC-5"]["figures"][0]["direction"] == "at_most"
    assert criteria["SC-5"]["met"] is True


def test_a_short_window_meets_sc4_without_claiming_it(run: Path) -> None:
    sc4 = by_id(criteria_summary(read_directory(run)))["SC-4"]
    assert sc4["met"] is True
    assert sc4["claimable"] is False
    assert sc4["caveat"] == "measured over 7 days, not the thirty SC-4 is over"


def test_sc8_is_not_claimed_before_its_effects_are_reviewed(run: Path) -> None:
    sc8 = by_id(criteria_summary(read_directory(run)))["SC-8"]
    assert sc8["met"] is True
    assert sc8["claimable"] is False
    assert [one["name"] for one in sc8["figures"]] == [
        "recall_obstruction",
        "wrong_fraction",
    ]
    assert sc8["figures"][0]["low"] == 0.7
    assert sc8["proposed"] is True


def test_the_summary_passes_the_reader_the_api_serves(run: Path) -> None:
    summary = criteria_summary(read_directory(run))
    served = PublicCriteria.model_validate_json(json.dumps(summary))
    assert len(served.criteria) == len(CRITERIA)


def test_the_reader_refuses_a_simulated_criterion_labelled_measured(
    run: Path,
) -> None:
    summary = criteria_summary(read_directory(run))
    summary["criteria"][4]["simulated"] = False  # type: ignore[index]
    with pytest.raises(ValueError, match="SC-5 is labelled simulated=False"):
        PublicCriteria.model_validate_json(json.dumps(summary))


def test_the_reader_refuses_a_number_beside_a_reason(run: Path) -> None:
    summary = criteria_summary(read_directory(run))
    summary["criteria"][1]["met"] = False  # type: ignore[index]
    with pytest.raises(ValueError, match="met"):
        PublicCriteria.model_validate_json(json.dumps(summary))


def test_a_row_of_the_wrong_shape_is_refused(tmp_path: Path) -> None:
    broken = {**SC1, "relative": "a lot"}
    run = publish(tmp_path, {"run.jsonl": [RUN], "scheduling.jsonl": [broken]})
    with pytest.raises(NotSummarisableError, match="cannot read"):
        criteria_summary(read_directory(run))


def test_a_directory_that_is_not_a_report_is_refused(tmp_path: Path) -> None:
    data = {"run.jsonl": canonical_line(RUN)}
    manifest = Manifest(
        kind="raw_snapshot",
        schema_revision="0032",
        since=SINCE,
        as_of=AS_OF,
        files=(file_entry("run.jsonl", data["run.jsonl"]),),
        created_at=AS_OF,
    )
    path = publish_directory(tmp_path, "raw", manifest, data).path
    with pytest.raises(NotSummarisableError, match="not an evaluation report"):
        criteria_summary(read_directory(path))


def test_the_command_writes_the_file(
    run: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "published" / "criteria.json"
    assert main(["report", "criteria", str(run), "--output", str(output)]) == 0
    assert json.loads(output.read_text())["schema"] == SCHEMA
    assert "SC-1     measured" in capsys.readouterr().out
