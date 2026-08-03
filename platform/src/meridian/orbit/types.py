"""Value types crossing the orbit module boundary.

Every ``datetime`` here is timezone-aware UTC. docs/DATA-MODEL.md says "all
timestamps UTC, no exceptions" — ruff's DTZ rules enforce that at construction
sites, and :func:`require_utc` enforces it at this boundary.

Angles are degrees. Azimuth is 0–360, elevation −90 to +90. Frequencies are Hz as
integers, never floats and never MHz.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

__all__ = [
    "DopplerSample",
    "ElementSet",
    "GroundSite",
    "LookAngle",
    "PassWindow",
    "TimingUncertainty",
    "require_utc",
]


def require_utc(t: datetime, field: str) -> datetime:
    """Return ``t`` unchanged, or raise if it is naive or not UTC.

    Coordinate frames are where silent bugs live, and a naive datetime is the
    cheapest way to introduce one — it will propagate happily and be wrong by
    whatever the local offset happens to be on the machine that ran it.
    """
    if t.tzinfo is None:
        raise ValueError(f"{field} is naive; all timestamps are timezone-aware UTC")
    if t.utcoffset() != UTC.utcoffset(None):
        raise ValueError(f"{field} is not UTC (offset {t.utcoffset()})")
    return t


@dataclass(frozen=True, slots=True)
class GroundSite:
    """A receiving location.

    ``alt_m`` is required rather than optional: MSP §4.1 says altitude materially
    affects pass geometry, and defaulting it to zero silently biases every pass
    prediction for a station on a rooftop at 920 m.
    """

    lat_deg: float
    lon_deg: float
    alt_m: float


@dataclass(frozen=True, slots=True)
class ElementSet:
    """A two-line element set with the epoch it was issued for.

    Element sets are never overwritten in the archive — the historical series is
    what makes uncertainty modelling possible — so this type carries the epoch
    rather than assuming the caller tracked which set it used.
    """

    satellite_id: str
    """``norad:NNNNN`` as text. Objects without NORAD ids exist."""

    epoch: datetime
    line1: str
    line2: str
    source: str = "celestrak"

    def age_s(self, at: datetime) -> float:
        """Seconds between this set's epoch and ``at``.

        Element-set age is a first-class feature everywhere in this project — it
        is the proxy for prediction error that SC-3 is measured against.
        """
        return (require_utc(at, "at") - require_utc(self.epoch, "epoch")).total_seconds()


@dataclass(frozen=True, slots=True)
class LookAngle:
    """Where to point, and how fast the range is changing, at one instant."""

    t: datetime
    azimuth_deg: float
    elevation_deg: float
    range_km: float
    range_rate_km_s: float
    """Positive means receding. Doppler shift is derived from this."""


@dataclass(frozen=True, slots=True)
class PassWindow:
    """A computed opportunity to receive one satellite from one site.

    A pass exists whether or not anyone observed it, which is exactly what the
    completeness ratio in docs/EVALUATION.md §4.1 requires — the denominator is
    computed by us, never taken from an archive.
    """

    satellite_id: str
    aos: datetime
    los: datetime
    max_elevation_deg: float
    max_elevation_at: datetime
    aos_azimuth_deg: float
    los_azimuth_deg: float
    element_set_epoch: datetime
    element_set_age_s: float
    """Age at AOS. Carried so timing error can be attributed to it later."""

    min_elevation_deg: float
    """The elevation floor this window was computed against.

    Without it a stored pass is uninterpretable: a 5-degree pass is absent from a
    10-degree search rather than nonexistent, and the completeness ratio would
    silently compare windows computed against different floors.
    """


@dataclass(frozen=True, slots=True)
class DopplerSample:
    """Frequency offset from the nominal centre frequency at one instant.

    Offset rather than absolute frequency, to match the MSP §4.4 observation wire
    format so platform and station report the same quantity.
    """

    t: datetime
    offset_hz: float


@dataclass(frozen=True, slots=True)
class TimingUncertainty:
    """The platform's stated 1σ confidence in a predicted pass boundary.

    ``method`` is recorded rather than assumed. Phase 1 ships a documented prior;
    Phase 2 replaces it with a model fitted to measured timing error. SC-3 is a
    comparison between the two, which is impossible if stored predictions do not
    say which produced them.
    """

    sigma_s: float
    method: str
    element_set_age_s: float
