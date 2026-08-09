"""How far out a predicted pass boundary is likely to be, given the element set.

A station needs this to decide how much extra to record either side of a
predicted acquisition. Record too little and passes start before the receiver
does; record too much and every pass costs storage it did not need. MSP §4.3
carries the figure to stations as ``timing_uncertainty_s``, and this module is
where it comes from.

Phase 1 answers from a published prior rather than from measurement, because
there is nothing measured yet. The answer therefore carries a ``method`` string
naming which model produced it — Phase 2 fits a model to observed timing error,
and SC-3 is the comparison between the two, which is impossible if a stored
prediction cannot say what produced it.

Arithmetic on an age in seconds: no propagator, no clock, no I/O.

Reference: Vallado, Crawford, Hujsak & Kelso, "Revisiting Spacetrack Report #3",
AIAA 2006-6753 — the SGP4 accuracy figures the constants below come from.
"""

from __future__ import annotations

from meridian.orbit.types import TimingUncertainty

__all__ = [
    "ALONG_TRACK_SPEED_KM_S",
    "ERROR_GROWTH_KM_PER_DAY",
    "POSITION_ERROR_AT_EPOCH_KM",
    "SECONDS_PER_DAY",
    "TIMING_PRIOR_METHOD",
    "timing_uncertainty_at_age",
]

SECONDS_PER_DAY = 86_400.0

POSITION_ERROR_AT_EPOCH_KM = 1.0
"""Typical along-track error of an SGP4 prediction at the element set's epoch.

About a kilometre for low Earth orbit, per Vallado et al. (2006). It is not
zero even at epoch: a two-line element set is a fitted mean orbit, not an
observation, and the fit has residuals."""

ERROR_GROWTH_KM_PER_DAY = 3.0
"""How fast that error grows as the element set ages.

Vallado et al. report roughly 1–3 km per day for low Earth orbit. **The top of
the range is used deliberately.** The two directions are not symmetric: an
overstated figure costs a station some disk, while an understated one has it
start recording after the pass began, and a pass is eight to fifteen minutes
that never repeats."""

ALONG_TRACK_SPEED_KM_S = 7.5
"""Speed at which a satellite covers along-track error, converting km into
seconds.

The orbital speed ``sqrt(mu / a)`` at 800 km — ``a`` = 7171 km, ``mu`` =
398 600 km^3/s^2 — is 7.46 km/s, and this rounds it. That altitude is the band
the 137 MHz weather satellites occupy, which is what this project receives."""

TIMING_PRIOR_METHOD = "vallado2006-along-track-v1"
"""Names the model and its version, so a stored prediction says what produced it.

SC-3 compares this prior against a model fitted to measured timing error; the
comparison needs both to be identifiable in the archive years later."""


def timing_uncertainty_at_age(element_set_age_s: float) -> TimingUncertainty:
    """1 sigma confidence in a pass boundary predicted from an element set this old.

    An along-track position error moves the satellite forward or back along the
    path it will follow anyway, so the instant it reaches the horizon shifts by
    that distance divided by its speed. That is the whole model: the position
    error grows linearly with age, and dividing by orbital speed turns it into
    seconds.

    Args:
        element_set_age_s: Seconds between the element set's epoch and the
            instant being predicted, from
            :meth:`meridian.orbit.types.ElementSet.age_s`. Negative when
            predicting before the epoch, which degrades the same way — SGP4 is
            no more accurate propagating backwards than forwards — so the
            magnitude is what counts.

    Returns:
        A :class:`~meridian.orbit.types.TimingUncertainty` carrying the figure,
        the age it was computed from, and :data:`TIMING_PRIOR_METHOD`.

    Note:
        **This models along-track error only, and is therefore a floor rather
        than a full account.** A pass boundary is a shallow crossing — the
        satellite approaches the horizon at a slight angle — so cross-track
        error also moves the crossing instant, by more than it moves the
        satellite. Estimating that needs measured timing error against predicted
        boundaries, which is exactly what Phase 2 collects and what
        ``method`` exists to let SC-3 compare against.

        The figure is never zero. At epoch it is 0.13 s, from the metre-scale
        residuals of the element set fit rather than from nothing.
    """
    age_days = abs(element_set_age_s) / SECONDS_PER_DAY
    position_error_km = POSITION_ERROR_AT_EPOCH_KM + ERROR_GROWTH_KM_PER_DAY * age_days
    return TimingUncertainty(
        sigma_s=position_error_km / ALONG_TRACK_SPEED_KM_S,
        method=TIMING_PRIOR_METHOD,
        element_set_age_s=element_set_age_s,
    )
