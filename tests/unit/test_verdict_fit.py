"""``fit_verdict`` and ``evaluate_verdict`` — the verdict fitted and judged.

The world is :func:`conftest._verdict_world`: rated receptions whose usable
rate rises with SNR, a fifth without decoder statistics, weak ones with no SNR
and no product, and a few simulated ones that must never be read.

Reference: docs/DECISIONS.md D-078, D-162, D-260, D-261, D-262.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from meridian.cli import main
from meridian.datasets.canonical import canonical_bytes, canonical_line
from meridian.datasets.usable_labels import read_usable_labels
from meridian.prediction.fit import ModelFitError, fit_verdict
from meridian.prediction.splits import SplitError
from meridian.prediction.verdict_config import VerdictConfig
from meridian.prediction.verdict_evaluation import evaluate_verdict
from meridian.prediction.verdict_examples import (
    VerdictExamples,
    build_verdict_examples,
    learns_from,
)
from meridian.prediction.verdict_inputs import FULL, OUTCOME, ROUTES, SNR
from meridian.prediction.verdict_report import verdict_lines
from meridian.prediction.verdict_rows import read_receptions
from meridian.prediction.verdict_score import parse_verdict_model

AS_OF = datetime(2026, 9, 23, 6, 0, tzinfo=UTC)
"""The verdict world's snapshot instant, as ``conftest.AS_OF``."""

VERDICT_TRAIN_UNTIL = datetime(2026, 9, 12, tzinfo=UTC)
VERDICT_VALIDATE_UNTIL = datetime(2026, 9, 17, tzinfo=UTC)

CONFIG = VerdictConfig(
    train_until=VERDICT_TRAIN_UNTIL, validate_until=VERDICT_VALIDATE_UNTIL
)

Tables = Mapping[str, Sequence[Mapping[str, object]]]


def files_of(world: Tables) -> dict[str, bytes]:
    return {
        f"{name}.jsonl": b"".join(canonical_line(row) for row in rows)
        for name, rows in world.items()
    }


@pytest.fixture
def found(verdict_world: Tables) -> VerdictExamples:
    files = files_of(verdict_world)
    return build_verdict_examples(
        read_receptions(files), read_usable_labels(files), rubric="usable-1"
    )


def test_the_world_has_every_kind_of_example(found: VerdictExamples) -> None:
    routes = {one.route for one in found.examples}
    sources = {one.source for one in found.examples}

    assert routes == set(ROUTES)
    assert sources == {"rating", "no_product"}
    assert found.simulated > 0
    assert found.unrated > 0
    assert not any(one.reception.simulated for one in found.examples)


def test_each_route_learns_from_every_example_with_its_inputs(
    found: VerdictExamples,
) -> None:
    every = len(found.examples)
    with_snr = sum(one.route != OUTCOME for one in found.examples)
    full = sum(one.route == FULL for one in found.examples)

    assert len(learns_from(OUTCOME, found.examples)) == every
    assert len(learns_from(SNR, found.examples)) == with_snr
    assert len(learns_from(FULL, found.examples)) == full
    assert every > with_snr > full > 0


def test_the_fit_is_a_model_the_scorer_reads(found: VerdictExamples) -> None:
    fitted = fit_verdict(found, CONFIG, as_of=AS_OF)

    model = parse_verdict_model(canonical_bytes(fitted.document))

    assert set(model.routes) == set(ROUTES)
    assert model.method == fitted.document["method"]
    assert str(model.method).startswith("verdict-1:")
    assert model.partial_below == 0.5
    assert model.rubric == "usable-1"


def test_the_same_inputs_give_the_same_bytes(found: VerdictExamples) -> None:
    first = fit_verdict(found, CONFIG, as_of=AS_OF)
    second = fit_verdict(found, CONFIG, as_of=AS_OF)

    assert canonical_bytes(first.document) == canonical_bytes(second.document)


def test_a_different_fit_is_a_different_method(found: VerdictExamples) -> None:
    first = fit_verdict(found, CONFIG, as_of=AS_OF)
    other = fit_verdict(found, replace(CONFIG, inverse_regularisation=0.1), as_of=AS_OF)

    assert first.document["method"] != other.document["method"]


def test_the_test_span_never_reaches_the_fit(found: VerdictExamples) -> None:
    """D-162: flipping every test label leaves the fitted model unchanged."""
    flipped = VerdictExamples(
        examples=tuple(
            replace(one, usable=not one.usable)
            if one.reception.started_at >= VERDICT_VALIDATE_UNTIL
            else one
            for one in found.examples
        ),
        simulated=found.simulated,
        unrated=found.unrated,
        other_rubric=found.other_rubric,
    )

    first = fit_verdict(found, CONFIG, as_of=AS_OF).document
    second = fit_verdict(flipped, CONFIG, as_of=AS_OF).document

    assert first["routes"] == second["routes"]


def test_the_counts_say_what_was_left_out(found: VerdictExamples) -> None:
    counts = fit_verdict(found, CONFIG, as_of=AS_OF).counts

    assert counts["examples.simulated"] == found.simulated
    assert counts["examples.unrated"] == found.unrated
    assert counts["examples.train"] + counts["examples.validate"] + counts[
        "examples.test"
    ] == len(found.examples)
    assert 0 < counts["examples.test_rated"] < counts["examples.test"]


def test_simulated_receptions_alone_are_refused_as_simulated() -> None:
    nothing = VerdictExamples(examples=(), simulated=12, unrated=0, other_rubric=0)

    with pytest.raises(ModelFitError, match="12 receptions are simulated"):
        fit_verdict(nothing, CONFIG, as_of=AS_OF)


def test_a_route_with_too_few_examples_is_refused_by_name(
    found: VerdictExamples,
) -> None:
    """Without decoder statistics in validation, the full route cannot calibrate."""
    thinned = replace(
        found,
        examples=tuple(
            one
            for one in found.examples
            if one.route != FULL
            or not VERDICT_TRAIN_UNTIL
            <= one.reception.started_at
            < VERDICT_VALIDATE_UNTIL
        ),
    )

    with pytest.raises(ModelFitError, match="route full: validation holds 0"):
        fit_verdict(thinned, CONFIG, as_of=AS_OF)


def test_a_configuration_without_dates_is_refused(found: VerdictExamples) -> None:
    with pytest.raises(ModelFitError, match="names no train_until"):
        fit_verdict(found, VerdictConfig(), as_of=AS_OF)


def test_a_reception_after_as_of_cannot_be_split(found: VerdictExamples) -> None:
    with pytest.raises(SplitError):
        fit_verdict(found, CONFIG, as_of=AS_OF - timedelta(days=1))


def test_a_rating_under_another_rubric_is_left_out(verdict_world: Tables) -> None:
    files = files_of(verdict_world)
    other = build_verdict_examples(
        read_receptions(files), read_usable_labels(files), rubric="usable-2"
    )

    assert other.other_rubric > 0
    assert all(one.source == "no_product" for one in other.examples)


def test_evaluation_judges_the_test_span_twice(found: VerdictExamples) -> None:
    model = parse_verdict_model(
        canonical_bytes(fit_verdict(found, CONFIG, as_of=AS_OF).document)
    )

    evaluation = evaluate_verdict(
        model,
        found,
        train_until=VERDICT_TRAIN_UNTIL,
        validate_until=VERDICT_VALIDATE_UNTIL,
        as_of=AS_OF,
    )

    assert evaluation.every.n == len(evaluation.split.test)
    assert evaluation.rated is not None
    assert evaluation.rated.n < evaluation.every.n
    assert evaluation.every.skill is not None
    assert evaluation.every.skill > 0
    dimensions = {one.dimension for one in evaluation.every.segments}
    assert dimensions == {
        "station",
        "band",
        "data_type",
        "decoder_version",
        "decoder_statistics",
    }
    assert evaluation.below.n + evaluation.above.n > 0


def test_the_report_says_measured_and_names_both_judgements(
    found: VerdictExamples,
) -> None:
    fitted = fit_verdict(found, CONFIG, as_of=AS_OF)
    model = parse_verdict_model(canonical_bytes(fitted.document))
    evaluation = evaluate_verdict(
        model,
        found,
        train_until=VERDICT_TRAIN_UNTIL,
        validate_until=VERDICT_VALIDATE_UNTIL,
        as_of=AS_OF,
    )

    text = "\n".join(verdict_lines(evaluation, found, method=model.method))

    assert "measured receptions only" in text
    assert "every labelled reception" in text
    assert "rated receptions only" in text
    assert "archive receptions: none" in text
    assert "partial threshold 0.50" in text


def test_fit_and_evaluate_from_the_command(
    verdict_world: Tables,
    verdict_snapshot: Callable[[Tables], Path],
    datasets_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    snapshot = verdict_snapshot(dict(verdict_world))
    config = tmp_path / "verdict.toml"
    config.write_text(
        "train_until = 2026-09-12T00:00:00Z\nvalidate_until = 2026-09-17T00:00:00Z\n",
        encoding="utf-8",
    )
    root = ["--root", str(datasets_root)]

    assert main(["verdict", "fit", str(snapshot), "--config", str(config), *root]) == 0
    (published,) = (
        one
        for one in (datasets_root / "verdicts").iterdir()
        if not one.name.startswith(".")
    )
    assert main(["verdict", "evaluate", str(published), *root]) == 0

    out = capsys.readouterr().out
    assert "verdict model:" in out
    assert "method             verdict-1:" in out
    assert "every labelled reception" in out
    document = json.loads((published / "verdict.json").read_text(encoding="utf-8"))
    assert document["config_sha256"]
    assert document["snapshot_sha256"]
