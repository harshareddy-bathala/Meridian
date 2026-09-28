"""The configured model, loaded once and kept current — D-169.

A run with a model scores its candidates through
:class:`~meridian.prediction.live.LiveScorer`. Loading one reads the model
and, for a model that reads history, the newest labelled dataset and its raw
snapshot, then indexes them. That is too much to repeat every five minutes
for nothing, so :class:`ScorerSource` keeps the scorer it loaded and loads
again only when a newer labelled dataset has appeared: a round then reads the
dataset manifests, and nothing more, to know its history is current.

Reference: docs/DECISIONS.md D-168, D-169.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from meridian.prediction.live import (
    LiveScorer,
    Scored,
    load_live_scorer,
    newest_dataset_path,
)
from meridian.scheduler.objective import Yield, modelled
from meridian.scheduler.schedule_config import ScheduleConfig, check_model, model_path

__all__ = ["ScorerSource", "load_scorer", "yields_of"]


def load_scorer(config: ScheduleConfig, root: Path) -> LiveScorer | None:
    """The configured model, ready to score; ``None`` when none is configured.

    Raises:
        ScheduleConfigError: The model is another configuration's.
        LiveScoringError: It reads history, and there is none to read.
        DamagedSnapshotError: A directory is not what its manifest says.
        MalformedModelError: The model cannot be scored.
    """
    path = model_path(config, root)
    if path is None:
        return None
    scorer = load_live_scorer(path, root=root)
    check_model(config, scorer.model.configuration)
    return scorer


def yields_of(scored: Mapping[int, Scored]) -> dict[int, Yield]:
    """The objective's yield term for each scored pass, with its route."""
    return {
        pass_id: modelled(
            one.prediction.probability, one.prediction.path, one.prediction.reason
        )
        for pass_id, one in scored.items()
    }


class ScorerSource:
    """The configured scorer, reloaded when a newer labelled dataset appears."""

    def __init__(self, config: ScheduleConfig, root: Path) -> None:
        """Hold the configuration; nothing is read until the first round asks."""
        self._config = config
        self._root = root
        self._scorer: LiveScorer | None = None
        self._dataset: Path | None = None
        self._loaded = False

    def current(self) -> LiveScorer | None:
        """The scorer, over the newest history there is.

        Raises:
            As :func:`load_scorer`. A failed load leaves the newer dataset
            unrecorded, so the next round tries again rather than scoring with
            the history it had.
        """
        if self._config.model is None:
            return None
        newest = newest_dataset_path(self._root)
        if self._loaded and newest == self._dataset:
            return self._scorer
        self._scorer = load_scorer(self._config, self._root)
        self._dataset = newest
        self._loaded = True
        return self._scorer
