"""The configured model, loaded once and reloaded only for newer history (D-169)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from meridian.prediction.features import FEATURE_VERSION
from meridian.prediction.live import LiveScorer, LiveScoringError, Scored
from meridian.prediction.score import Linear, Model, Prediction
from meridian.scheduler import scoring
from meridian.scheduler.schedule_config import ScheduleConfig
from meridian.scheduler.scoring import ScorerSource, yields_of

LINEAR = Linear(
    features=("max_elevation_deg",),
    mean=(0.0,),
    scale=(1.0,),
    coefficients=(0.0,),
    intercept=0.0,
    calibration_a=1.0,
    calibration_b=0.0,
)
D = ScheduleConfig(configuration="D", model="models/d")


def a_scorer() -> LiveScorer:
    return LiveScorer(
        Model(
            configuration="A",
            reads_history=False,
            min_station_history=0,
            configured=LINEAR,
            fallback=None,
            feature_version=FEATURE_VERSION,
        ),
        bytes(32),
    )


def a_history_scorer() -> Any:
    """What the source reads of a scorer over history: that it reads history."""
    return SimpleNamespace(model=SimpleNamespace(reads_history=True))


class Loads:
    """Stands in for loading a scorer, counting how often it is asked."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        self.count = 0
        self.looked = 0
        self.fail = False
        self.history = True
        self.newest: Path | None = tmp_path / "evaluation" / "one"
        monkeypatch.setattr(scoring, "load_scorer", self.load)
        monkeypatch.setattr(scoring, "newest_dataset_path", self.newest_path)

    def newest_path(self, _root: Path) -> Path | None:
        self.looked += 1
        return self.newest

    def load(self, _config: ScheduleConfig, _root: Path) -> Any:
        self.count += 1
        if self.fail:
            raise LiveScoringError("the newest dataset is damaged")
        return a_history_scorer() if self.history else a_scorer()


@pytest.fixture
def loads(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Loads:
    return Loads(monkeypatch, tmp_path)


def test_no_model_configured_reads_nothing(loads: Loads, tmp_path: Path) -> None:
    assert ScorerSource(ScheduleConfig(), tmp_path).current() is None
    assert loads.count == 0


def test_the_scorer_is_kept_while_the_history_is_unchanged(
    loads: Loads, tmp_path: Path
) -> None:
    source = ScorerSource(D, tmp_path)

    first = source.current()
    again = source.current()

    assert first is again
    assert loads.count == 1


def test_a_newer_labelled_dataset_is_loaded_when_it_appears(
    loads: Loads, tmp_path: Path
) -> None:
    source = ScorerSource(D, tmp_path)
    first = source.current()

    loads.newest = tmp_path / "evaluation" / "two"
    second = source.current()

    assert second is not first
    assert loads.count == 2


def test_a_model_reading_no_history_is_loaded_once_and_never_looks_again(
    loads: Loads, tmp_path: Path
) -> None:
    """A new dataset changes nothing it reads, and a damaged one cannot stop it."""
    loads.history = False
    source = ScorerSource(ScheduleConfig(configuration="A", model="models/a"), tmp_path)
    first = source.current()
    loads.newest = tmp_path / "evaluation" / "two"

    again = source.current()

    assert again is first
    assert (loads.count, loads.looked) == (1, 1)


def test_a_failed_load_keeps_nothing_and_is_tried_again(
    loads: Loads, tmp_path: Path
) -> None:
    """A round must not go on scoring with a history it could not refresh."""
    source = ScorerSource(D, tmp_path)
    source.current()
    loads.newest = tmp_path / "evaluation" / "two"
    loads.fail = True

    with pytest.raises(LiveScoringError):
        source.current()
    loads.fail = False
    source.current()

    assert loads.count == 3


def test_a_prediction_becomes_the_yield_term_with_its_route() -> None:
    scored = {
        7: Scored(
            prediction=Prediction(0.25, "geometry_fallback", "a new station"),
            features={},
            station_history=0,
        )
    }

    (only,) = yields_of(scored).values()

    assert (only.probability, only.source, only.path, only.reason) == (
        0.25,
        "model",
        "geometry_fallback",
        "a new station",
    )
