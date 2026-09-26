"""``meridian.datasets.result`` and its reader — D-154's type, and reading it back.

Reference: docs/DECISIONS.md D-151, D-153, D-154.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from meridian.datasets.completeness import summarise
from meridian.datasets.completeness_report import report_lines
from meridian.datasets.evaluation import build_evaluation_dataset
from meridian.datasets.label_config import (
    CompletenessConfig,
    LabelConfig,
    LabelConfigError,
)
from meridian.datasets.publish import SnapshotDirectory, read_directory
from meridian.datasets.result import EvaluationResult, NotWeighted
from meridian.datasets.result_reader import NoSelectionError, read_results
from meridian.datasets.weighting import IpwDiagnostics

CREATED = datetime(2026, 9, 23, 7, 0, tzinfo=UTC)
EMPTY = summarise((), "own", CompletenessConfig())


@pytest.fixture
def dataset(
    raw_snapshot: Any, datasets_root: Path, archive_world: Any
) -> SnapshotDirectory:
    published = build_evaluation_dataset(
        read_directory(raw_snapshot(archive_world)),
        LabelConfig(),
        root=datasets_root,
        created_at=CREATED,
    )
    return read_directory(published.path)


# --- the type -----------------------------------------------------------------


def test_a_result_cannot_be_built_without_its_completeness() -> None:
    with pytest.raises(TypeError, match="completeness"):
        EvaluationResult(
            population="own",
            completeness=None,  # type: ignore[arg-type]
            weighting=NotWeighted("prospective, policy assigned by us"),
        )


def test_a_result_cannot_carry_nothing_for_its_weights() -> None:
    with pytest.raises(TypeError, match="weights"):
        EvaluationResult(
            population="own",
            completeness=EMPTY,
            weighting=None,  # type: ignore[arg-type]
        )


def test_not_weighted_must_say_why() -> None:
    with pytest.raises(ValueError, match="say why"):
        NotWeighted("  ")


def test_a_result_cannot_borrow_another_population_s_completeness() -> None:
    with pytest.raises(ValueError, match="carries the completeness of own"):
        EvaluationResult(
            population="archive", completeness=EMPTY, weighting=NotWeighted("none")
        )


def test_not_weighted_is_never_unreliable() -> None:
    result = EvaluationResult(
        population="own", completeness=EMPTY, weighting=NotWeighted("none")
    )

    assert result.unreliable is False
    assert result.parameters()["weighting"] == {"not_weighted": "none"}


# --- reading it back ----------------------------------------------------------


def test_read_back_is_what_label_wrote(dataset: SnapshotDirectory) -> None:
    results = read_results(dataset)

    assert {one.population: one.parameters() for one in results} == dict(
        dataset.manifest.summary["populations"]  # type: ignore[call-overload]
    )
    assert isinstance(results[0].weighting, IpwDiagnostics)
    assert results[0].unreliable is True


def test_another_threshold_judges_the_days_again(dataset: SnapshotDirectory) -> None:
    """The archive's first day is 2/3: out at 0.8, in at 0.6 — and at exactly 2/3."""
    at_default = read_results(dataset)[1].completeness
    lower = read_results(dataset, threshold=0.6)[1].completeness

    assert at_default.statuses["below_threshold"] == 1
    assert lower.statuses["below_threshold"] == 0
    assert lower.statuses["retained"] == 2
    assert lower.threshold == 0.6
    assert lower.statuses["inactive"] == at_default.statuses["inactive"] == 1
    assert (
        read_results(dataset, threshold=2 / 3)[1].completeness.statuses["retained"] == 2
    )


def test_a_threshold_outside_zero_to_one_is_refused(
    dataset: SnapshotDirectory,
) -> None:
    with pytest.raises(LabelConfigError, match=r"outside 0\.\.1"):
        read_results(dataset, threshold=1.5)


def test_a_raw_snapshot_has_no_results(raw_snapshot: Any, world: Any) -> None:
    with pytest.raises(NoSelectionError, match="label it first"):
        read_results(read_directory(raw_snapshot(world)))


def test_a_directory_of_another_kind_is_named_for_what_it_is(
    dataset: SnapshotDirectory,
) -> None:
    """A model is not a raw snapshot, and labelling it would not help."""
    model = replace(dataset, manifest=replace(dataset.manifest, kind="model"))

    with pytest.raises(NoSelectionError, match="is a model, not an evaluation"):
        read_results(model)


def test_a_dataset_labelled_before_stage_16_is_sent_back(
    dataset: SnapshotDirectory,
) -> None:
    older = replace(dataset, manifest=replace(dataset.manifest, summary={}))

    with pytest.raises(NoSelectionError, match="label its raw snapshot again"):
        read_results(older)


# --- the report ---------------------------------------------------------------


def test_the_report_states_everything_d151_and_d153_name(
    dataset: SnapshotDirectory,
) -> None:
    text = "\n".join(report_lines(read_results(dataset)))

    for phrase in (
        "our stations",
        "archive stations",
        "inactive 1",
        "deciles",
        "histogram (tenths)",
        "0.5: ",
        "0.9: ",
        "unweighted rate",
        "effective n",
        "unsupported 1",
        "overlap not",
    ):
        assert phrase in text


def test_an_unreliable_rate_says_so_on_its_own_line(
    dataset: SnapshotDirectory,
) -> None:
    lines = report_lines(read_results(dataset))

    own, archive = [one for one in lines if one.startswith("  weighted rate")]
    assert own.endswith("UNRELIABLE")
    assert archive.endswith("UNRELIABLE")


def test_a_result_without_weights_prints_its_reason() -> None:
    result = EvaluationResult(
        population="own",
        completeness=EMPTY,
        weighting=NotWeighted("prospective, policy assigned by us"),
    )

    assert report_lines([result])[-1] == (
        "  weighting          not weighted: prospective, policy assigned by us"
    )
