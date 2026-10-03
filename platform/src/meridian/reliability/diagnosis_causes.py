"""One test per cause: does the evidence show it, and how strongly.

Each test reads a :class:`~meridian.reliability.diagnosis_evidence.LossEvidence`
and returns a :class:`~meridian.reliability.diagnosis_evidence.Candidate`:
whether it fired, a support, and what it found, which is recorded whether it
fired or not. **A test that has nothing to read does not fire**, and says so:
a missing noise floor is not a normal one, and *undetermined* is a correct
answer (D-273).

Support is ``r / (1 + r)`` for a test that passed its threshold ``r`` times
over, so it is ½ at the threshold and rises towards 1; a categorical answer —
the station's own report, the catalogue's flag — is 1.

* **station not listening** — Stage 20's class, read and never restated: not
  confirmed listening, or unavailable because the station never began
  (D-273).
* **satellite silent** — the catalogue says the transmitter is off, or no other
  station heard it near the pass and enough others listened and heard nothing
  (D-276). Never for a reception that heard the satellite itself: the
  catalogue holds only today's flag, and a signal heard is its own proof.
* **obstruction** — signal lost inside sectors the station's own loss map
  marks, and not explained by a raised floor (D-274).
* **interference** — a floor raised against the station's own at the same gain
  (D-275).
* **timing fault** — the station's clock, its listening or its recording off by
  more than the stated timing uncertainty allows (D-277).

Standard library only (D-180).

Reference: docs/DECISIONS.md D-102, D-147, D-273 to D-277.
"""

from __future__ import annotations

import statistics
from datetime import datetime

from meridian.reliability.config import DiagnosisConfig
from meridian.reliability.diagnosis_evidence import (
    Candidate,
    LossEvidence,
)
from meridian.reliability.obstruction_map import build_map, lost_where_heard
from meridian.reliability.satellite_silence import SIGNAL, judge_satellite

__all__ = [
    "interference",
    "obstruction",
    "satellite_silent",
    "station_not_listening",
    "support_of",
    "timing_fault",
]

NOT_BEGUN = frozenset({"not_attempted", None})
"""Reports that say the station never began: none at all, or its own word."""


def support_of(ratio: float) -> float:
    """The support of a test that passed its threshold ``ratio`` times over."""
    return round(ratio / (1.0 + ratio), 6)


def station_not_listening(
    evidence: LossEvidence, _config: DiagnosisConfig
) -> Candidate:
    """Stage 20 says the station was not listening, or never began."""
    listening = evidence.listening
    found: dict[str, object] = {
        "classification_id": listening.classification_id,
        "classification": listening.classification,
        "listening_confirmed": listening.listening_confirmed,
        "heard_during_window": listening.heard_during_window,
        "outcome": listening.outcome,
    }
    fired = listening.classification == "station_not_confirmed_listening" or (
        listening.classification == "station_unavailable"
        and (listening.outcome in NOT_BEGUN or not listening.heard_during_window)
    )
    return Candidate("station_not_listening", fired, 1.0 if fired else 0.0, found)


def satellite_silent(evidence: LossEvidence, config: DiagnosisConfig) -> Candidate:
    """The transmitter was off: the catalogue says so, or the network heard nothing."""
    counts = evidence.satellite
    found: dict[str, object] = {
        "catalogue_active": counts.catalogue_active,
        "signals": counts.signals,
        "silences": counts.silences,
    }
    if evidence.listening.outcome in SIGNAL:
        # Whatever the catalogue says today, this reception heard it.
        found["reason"] = "heard the satellite"
        return Candidate("satellite_silent", False, 0.0, found)
    if counts.catalogue_active is False:
        found["reason"] = "catalogue"
        return Candidate("satellite_silent", True, 1.0, found)
    ratio = _network_silence(evidence, config, found)
    if ratio is None:
        return Candidate("satellite_silent", False, 0.0, found)
    return Candidate("satellite_silent", True, support_of(ratio), found)


def _network_silence(
    evidence: LossEvidence, config: DiagnosisConfig, found: dict[str, object]
) -> float | None:
    """How many times over other stations' silence names the satellite, if it does.

    Only for a confirmed silence on a pass high enough to say something (D-276).
    What was read, and why it did not count, goes into ``found``.
    """
    counts, listening = evidence.satellite, evidence.listening
    peak = counts.peak_elevation_deg
    found["peak_elevation_deg"] = peak
    if listening.outcome != "no_signal" or not listening.listening_confirmed:
        found["reason"] = "not a confirmed silence"
        return None
    if peak is None or peak < config.silent_min_elevation_deg:
        found["reason"] = "too low for its silence to say anything"
        return None
    state = judge_satellite(
        signals=counts.signals,
        silences=counts.silences,
        min_silent_attempts=config.silent_min_attempts,
    )
    found["state"] = state
    return counts.silences / config.silent_min_attempts if state == "silent" else None


def obstruction(evidence: LossEvidence, config: DiagnosisConfig) -> Candidate:
    """Signal lost inside sectors the station's own history marks."""
    sky = evidence.sky
    found: dict[str, object] = {"samples": len(sky), "history": len(evidence.history)}
    if not sky:
        found["reason"] = "no placed samples"
        return Candidate("obstruction", False, 0.0, found)
    if _lift(evidence, config) is not None:
        found["reason"] = "floor raised"
        return Candidate("obstruction", False, 0.0, found)
    loss_map = build_map(
        evidence.history,
        baseline_dbfs=evidence.noise.baseline_dbfs,
        declared=evidence.declared,
        config=config,
    )
    found["marked"] = loss_map.marked()
    found["declared_bins"] = len(evidence.declared)
    if any(one.snr_db >= config.heard_snr_db for one in sky):
        lost = lost_where_heard(sky, config)
        inside = [
            i for i in lost if loss_map.covers(sky[i].azimuth_deg, sky[i].elevation_deg)
        ]
        found.update(lost=len(lost), lost_inside=len(inside))
        ratio = len(inside) / config.obstruction_min_samples
    else:
        audible = [
            s for s in sky if s.elevation_deg >= config.audible_min_elevation_deg
        ]
        inside_count = sum(
            loss_map.covers(s.azimuth_deg, s.elevation_deg) for s in audible
        )
        share = inside_count / len(audible) if audible else 0.0
        found.update(
            audible=len(audible), audible_inside=inside_count, share=round(share, 4)
        )
        ratio = share / config.obstruction_absent_share
    if ratio < 1.0:
        return Candidate("obstruction", False, 0.0, found)
    return Candidate("obstruction", True, support_of(ratio), found)


def interference(evidence: LossEvidence, config: DiagnosisConfig) -> Candidate:
    """The floor was raised against the station's own, at the same gain."""
    noise = evidence.noise
    found: dict[str, object] = {
        "floor_dbfs": noise.floor_dbfs,
        "gain_db": noise.gain_db,
        "baseline_dbfs": noise.baseline_dbfs,
        "baseline_count": noise.baseline_count,
    }
    if noise.cell is not None:
        cell = noise.cell
        found["cell"] = {
            "profile_id": cell.profile_id,
            "azimuth_deg": cell.azimuth_deg,
            "hour_start": cell.hour_start,
            "noise_lift_db": cell.noise_lift_db,
            "sample_count": cell.sample_count,
            "same_gain": _within(noise.gain_db, cell.gain_min_db, cell.gain_max_db),
        }
    lift = _lift(evidence, config)
    if lift is None:
        return Candidate("interference", False, 0.0, found)
    found["lift_db"] = round(lift, 3)
    ratio = lift / config.interference_lift_db
    return Candidate("interference", True, support_of(ratio), found)


def timing_fault(evidence: LossEvidence, config: DiagnosisConfig) -> Candidate:
    """The station's clock, listening or recording disagreed with the window."""
    timing = evidence.timing
    start, end = timing.window
    allowed = timing.uncertainty_s + config.timing_tolerance_s
    deviations: dict[str, float] = {}
    if timing.listening_span is not None:
        first, last = timing.listening_span
        # Before the window opened, or after it closed: either way, elsewhere.
        deviations["listening_s"] = max(
            _seconds(first, start), _seconds(end, last), 0.0
        )
    if timing.skews_s:
        deviations["clock_skew_s"] = abs(statistics.median(timing.skews_s))
    if timing.reported_offsets:
        deviations["reported_offset_s"] = max(
            abs(offset) - (uncertainty or 0.0)
            for offset, uncertainty in timing.reported_offsets
        )
    if timing.recording is not None:
        began, ended = timing.recording
        early, late = _seconds(start, began), _seconds(ended, end)
        # Shifted only if both ends moved the same way, as a wrong clock moves them.
        deviations["recording_s"] = max(min(early, -late), min(-early, late), 0.0)
    found: dict[str, object] = {
        "allowed_s": round(allowed, 3),
        **{name: round(value, 3) for name, value in deviations.items()},
    }
    worst = max(deviations.values(), default=0.0)
    if allowed <= 0 or worst <= allowed:
        return Candidate("timing_fault", False, 0.0, found)
    return Candidate("timing_fault", True, support_of(worst / allowed), found)


def _lift(evidence: LossEvidence, config: DiagnosisConfig) -> float | None:
    """How far the floor stood above the baseline, if far enough to be raised."""
    noise = evidence.noise
    if (
        noise.floor_dbfs is None
        or noise.baseline_dbfs is None
        or noise.baseline_count < config.interference_min_baseline
    ):
        return None
    lift = noise.floor_dbfs - noise.baseline_dbfs
    return lift if lift >= config.interference_lift_db else None


def _within(value: float | None, low: float | None, high: float | None) -> bool | None:
    if value is None or low is None or high is None:
        return None
    return low <= value <= high


def _seconds(earlier: datetime, later: datetime) -> float:
    """How far ``later`` is after ``earlier``, in seconds; negative if before."""
    return (later - earlier).total_seconds()
