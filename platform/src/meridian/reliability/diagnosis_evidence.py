"""What a loss diagnosis reads, and what each of its tests answers.

The evidence a diagnosis is given is gathered from Meridian's own records by
the live path (:mod:`~meridian.reliability.diagnosis_gather`); the rules that
read it (:mod:`~meridian.reliability.diagnosis_causes`) see only these types.
So the rules can be tested on evidence written by hand, and no rule can reach a
database, an archive or the simulator's ledger to look for its answer
(D-102, D-105).

Standard library only, as the classification is (D-180).

Reference: docs/DECISIONS.md D-102, D-104, D-105, D-272 to D-277.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

__all__ = [
    "CAUSES",
    "Candidate",
    "Cause",
    "HistoryPass",
    "HorizonFloor",
    "ListeningEvidence",
    "LossEvidence",
    "NoiseReading",
    "ProfileCell",
    "SatelliteCounts",
    "SkySample",
    "TimingEvidence",
]

Cause = Literal[
    "satellite_silent",
    "station_not_listening",
    "obstruction",
    "interference",
    "timing_fault",
    "undetermined",
]
"""``loss_diagnoses.cause``: five causes, or *undetermined* (D-104)."""

CAUSES: tuple[Cause, ...] = (
    "satellite_silent",
    "station_not_listening",
    "obstruction",
    "interference",
    "timing_fault",
)
"""The five a test exists for, in the order every record lists them."""


@dataclass(frozen=True, slots=True)
class SkySample:
    """One SNR sample of a reception, placed in the station's sky."""

    t: datetime
    snr_db: float
    azimuth_deg: float
    elevation_deg: float


@dataclass(frozen=True, slots=True)
class ListeningEvidence:
    """What Stage 20 concluded about the pass, read and never restated (D-180)."""

    classification_id: int
    classification: str
    """The pass's class under the deployed classification method."""

    outcome: str | None
    """This reception's outcome; ``None`` when nothing was reported."""

    heard_during_window: bool
    listening_confirmed: bool | None


@dataclass(frozen=True, slots=True)
class SatelliteCounts:
    """Other receptions of the satellite near the pass, by D-147's rules."""

    catalogue_active: bool | None
    """The transmitter's ``active`` flag as the catalogue holds it now, which is
    the only state it holds; ``None`` if the transmitter is unknown."""

    signals: int
    """Other physical passes, of this population, that heard the satellite."""

    silences: int
    """Other physical passes, of this population, that heard nothing while the
    registry confirms they were listening."""


@dataclass(frozen=True, slots=True)
class HorizonFloor:
    """One declared horizon bin: a floor that holds clockwise for its width."""

    azimuth_deg: float
    width_deg: float
    min_elevation_deg: float


@dataclass(frozen=True, slots=True)
class HistoryPass:
    """One of the station's own earlier receptions, for its loss map."""

    samples: tuple[SkySample, ...]
    noise_floor_dbfs: float | None
    """At the station's usual gain, or ``None``: a pass whose floor cannot be
    compared is left out of the map, since a raised floor explains a loss."""


@dataclass(frozen=True, slots=True)
class ProfileCell:
    """The learned interference cell the pass fell in, cited (D-275)."""

    profile_id: int
    azimuth_deg: float
    azimuth_width_deg: float
    hour_start: int
    hour_width: int
    noise_lift_db: float
    sample_count: int
    gain_min_db: float | None
    gain_max_db: float | None


@dataclass(frozen=True, slots=True)
class NoiseReading:
    """This reception's floor against the station's own, at the same gain."""

    floor_dbfs: float | None
    gain_db: float | None
    baseline_dbfs: float | None
    """Median floor of the station's other observations at this gain, over the
    lookback; ``None`` with none."""

    baseline_count: int
    cell: ProfileCell | None = None


@dataclass(frozen=True, slots=True)
class TimingEvidence:
    """Every trace of the station's clock near the pass (D-277)."""

    window: tuple[datetime, datetime]
    """The assignment's window."""

    uncertainty_s: float
    """The assignment's stated timing uncertainty, 1σ; 0 when none was stated."""

    listening_span: tuple[datetime, datetime] | None
    """When the first and last heartbeat naming this assignment as listening
    arrived, by the platform's clock; ``None`` if none did."""

    skews_s: tuple[float, ...]
    """``sent_at − received_at`` of each heartbeat near the window."""

    reported_offsets: tuple[tuple[float, float | None], ...]
    """``(clock_offset_s, clock_uncertainty_s)`` of each heartbeat near the
    window that reported one."""

    recording: tuple[datetime, datetime] | None
    """The observation's own window; ``None`` with no observation."""


@dataclass(frozen=True, slots=True)
class LossEvidence:
    """Everything one diagnosis reads, about one failed or partial reception."""

    listening: ListeningEvidence
    satellite: SatelliteCounts
    noise: NoiseReading
    timing: TimingEvidence
    sky: tuple[SkySample, ...] = ()
    """This reception's samples, placed; empty without an observation or a
    track."""

    history: tuple[HistoryPass, ...] = ()
    declared: tuple[HorizonFloor, ...] = ()


@dataclass(frozen=True, slots=True)
class Candidate:
    """One cause's test: whether it fired, how strongly, and what it found."""

    cause: Cause
    fired: bool
    support: float
    """0 when it did not fire; in ``[0.5, 1]`` when it did, rising with how far
    the evidence passed the threshold; 1 for a categorical answer."""

    found: dict[str, object] = field(default_factory=dict)
    """What the test read and concluded, for ``candidates_json``: JSON values."""
