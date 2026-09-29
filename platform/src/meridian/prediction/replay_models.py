"""The models a retrospective comparison replays, checked before anything is read.

A comparison is only a comparison if its schedulers are judged on one span
(D-172). So A's, C's and D's models must each be the configuration it is named
as, each fitted on the dataset being replayed, and all three of one population
with one pair of split dates; their test span is then the one they share.
B names no model: its probabilities are A's (D-160).

Reference: docs/DECISIONS.md D-160, D-162, D-163, D-172.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from meridian.datasets.publish import SnapshotDirectory
from meridian.prediction.lineage import config_of
from meridian.prediction.model_config import ModelConfig
from meridian.prediction.model_files import read_model
from meridian.prediction.score import Model

__all__ = [
    "MODELLED",
    "DamagedReplayError",
    "HeldModel",
    "ReplayError",
    "check_models",
]

MODELLED = ("A", "C", "D")
"""The configurations with a model of their own; B's is A's (D-160)."""


class ReplayError(ValueError):
    """A dataset or a model the comparison cannot replay, and why."""


class DamagedReplayError(ReplayError):
    """A directory that is not what its manifest says."""


@dataclass(frozen=True, slots=True)
class HeldModel:
    """A model, and the verified directory it was read from."""

    model: Model
    directory: SnapshotDirectory


def check_models(
    paths: Mapping[str, Path], dataset_sha256: bytes
) -> tuple[dict[str, HeldModel], datetime]:
    """A's, C's and D's models, checked against the dataset and each other.

    Returns:
        The models, and where their shared test span begins.

    Raises:
        ReplayError: A model missing or extra, another configuration's, fitted
            on another dataset, or not sharing the others' population and
            dates; or models of the archive, or with no split dates.
        DamagedSnapshotError: A model directory is not what its manifest says.
    """
    if sorted(paths) != list(MODELLED):
        message = (
            f"the comparison names models for {', '.join(MODELLED)}, and was"
            f" given {', '.join(sorted(paths)) or 'none'}; B is scored by A's"
        )
        raise ReplayError(message)
    held: dict[str, HeldModel] = {}
    configs: dict[str, ModelConfig] = {}
    for name in MODELLED:
        fitted = read_model(paths[name])
        if fitted.model.configuration != name:
            message = (
                f"{paths[name]} is configuration {fitted.model.configuration},"
                f" named as {name}'s model"
            )
            raise ReplayError(message)
        if fitted.directory.manifest.derived_from != dataset_sha256:
            message = f"{paths[name]} was not fitted on the dataset being replayed"
            raise ReplayError(message)
        held[name] = HeldModel(fitted.model, fitted.directory)
        configs[name] = config_of(fitted.directory)
    shared = {
        (one.population, one.train_until, one.validate_until)
        for one in configs.values()
    }
    if len(shared) != 1:
        message = (
            "the models differ in population or split dates, so they have no"
            " test span in common; fit them with one set of dates"
        )
        raise ReplayError(message)
    config = configs["D"]
    if config.population != "own":
        message = (
            "the comparison replays our own stations, and these models are the"
            f" {config.population} population"
        )
        raise ReplayError(message)
    if config.validate_until is None:
        message = "the models name no split dates, so they have no test span (D-162)"
        raise ReplayError(message)
    return held, config.validate_until
