"""The configured model, loaded once and reloaded only for newer history (D-169)."""

from __future__ import annotations

from pathlib import Path

import pytest

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
        ),
        bytes(32),
    )


class Loads:
    """Stands in for loading a scorer, counting how often it is asked."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        self.count = 0
        self.fail = False
        self.newest: Path | None = tmp_path / "evaluation" / "one"
        monkeypatch.setattr(scoring, "load_scorer", self.load)
        monkeypatch.setattr(scoring, "newest_dataset_path", lambda _root: self.newest)

    def load(self, _config: ScheduleConfig, _root: Path) -> LiveScorer:
        self.count += 1
        if self.fail:
            raise LiveScoringError("the newest dataset is damaged")
        return a_scorer()


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
