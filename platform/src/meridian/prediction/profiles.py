"""The learned environment — what a station's own receptions say about its sky.

D-159's four features, each inferred from outcomes, never declared. A horizon
mask a station declares (D-031) is a capability; what its detections say is
this, and the two are kept apart.

* **Horizon profile.** Where in the sky the station first hears satellites.
  Each settled report with a ``first_detection_at`` is placed on its pass's
  frozen track (D-158): the azimuth and elevation at that instant. Per 10°
  sector, the learned horizon is the lower quartile of those elevations, shrunk
  towards 0° by the sector's count. A pass's feature is the share of its track
  above the horizon of the sector each sample is in.
* **Interference profile.** How much louder the noise floor is when a pass
  peaks in one part of the sky, at one time of day. Each settled report's
  ``noise_floor_dbfs`` is filed under its pass's peak sector (45°) and 4-hour
  band of local solar hour; a cell's value is its median minus the station's
  median, shrunk towards 0 dB by its count. 45° rather than 10°: a report
  carries one noise floor for the whole pass, so the finer grid would be cells
  of one reading each.
* **Timing error.** The median of ``first_detection_at − aos`` over the
  station's last :data:`~meridian.prediction.history.RECENT` detections.
* **Element-set divergence.** How far apart the predictions of this rise put
  its ``aos``, over those made before it (D-148). It needs no history: it is a
  direct reading of how much the orbit estimate moved.

**Only the past** (D-157): a report counts once its pass's ``los`` plus the
settle margin has passed, as in :mod:`meridian.prediction.history`. **Never
missing** (D-161): an empty cell is at its prior, beside a count of zero.

Which report of a physical pass is read: the most informative of its
assignments' latest revisions, as D-146 pools them. A simulated report is
never read (D-078).

Reference: docs/DECISIONS.md D-031, D-078, D-146, D-148, D-157, D-159, D-161.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import median

from meridian.datasets.labels import LabelledPass
from meridian.datasets.pooled_evidence import OUTCOME_ORDER
from meridian.prediction.feature_rows import FeatureRows, PassGeometry, Reading
from meridian.prediction.geometry import peak_and_sweep, sector, track_at
from meridian.prediction.history import RECENT

__all__ = ["ENVIRONMENT", "Environment"]

HORIZON_SECTOR_DEG = 10.0
NOISE_SECTOR_DEG = 45.0
HOUR_BAND_H = 4
SHRINK = 5
"""Pseudo-count: a cell of five readings is trusted halfway."""
_LOWER_QUARTILE = 0.25
_A_SPREAD = 2
"""Predictions needed before their rises can disagree."""

ENVIRONMENT: tuple[tuple[str, str], ...] = (
    ("horizon_clear_share", "share of the track above the learned horizon"),
    ("horizon_n", "detections behind the sectors the pass crosses"),
    ("interference_db", "noise at the peak's sector and hour, over the median"),
    ("interference_n", "readings behind that cell"),
    ("timing_error_s", f"median first detection minus aos, last {RECENT}"),
    ("timing_error_n", "how many that median is over"),
    ("element_set_divergence_s", "aos spread across predictions made before it"),
)
"""The learned environment's features, in the order :meth:`values` returns."""


@dataclass(frozen=True, slots=True)
class _Heard:
    """One settled report, placed on the sky."""

    settled_at: datetime
    detection: tuple[float, float] | None
    """Azimuth and elevation at first detection, where it was placed."""

    timing_error_s: float | None
    noise_dbfs: float | None
    noise_cell: tuple[int, int]


class Environment:
    """Every station's settled reports, answering only for the past."""

    def __init__(
        self,
        labelled: Iterable[LabelledPass],
        rows: FeatureRows,
        *,
        settle_margin_s: int,
    ) -> None:
        """Place every measured, labelled pass's report, by station and time."""
        margin = timedelta(seconds=settle_margin_s)
        held: dict[str, list[_Heard]] = {}
        for one in labelled:
            report = _report(one, rows)
            geometry = rows.geometry.get(one.pass_id)
            if report is None or geometry is None or one.label is None:
                continue
            held.setdefault(one.station_id, []).append(
                _place(one, report, geometry, rows, one.los + margin)
            )
        self._rows = rows
        self._heard = {
            key: sorted(value, key=lambda one: one.settled_at)
            for key, value in held.items()
        }
        self._times = {
            key: [one.settled_at for one in value] for key, value in self._heard.items()
        }

    def values(self, one: LabelledPass, geometry: PassGeometry) -> tuple[float, ...]:
        """The pass's environment features at its own ``aos``, in ENVIRONMENT order."""
        heard = self._before(one.station_id, one.aos)
        clear, horizon_n = _horizon(heard, geometry)
        noise, noise_n = _interference(
            heard, _noise_cell(one, geometry, self._rows.longitudes)
        )
        timing = [one.timing_error_s for one in heard if one.timing_error_s is not None]
        timing = timing[-RECENT:]
        return (
            clear,
            float(horizon_n),
            noise,
            float(noise_n),
            median(timing) if timing else 0.0,
            float(len(timing)),
            _divergence(one, self._rows),
        )

    def _before(self, station_id: str, at: datetime) -> Sequence[_Heard]:
        times = self._times.get(station_id)
        if times is None:
            return ()
        return self._heard[station_id][: bisect_right(times, at)]


def _report(one: LabelledPass, rows: FeatureRows) -> Reading | None:
    """The most informative measured report of any prediction of this rise."""
    if one.simulated:
        return None
    readings = [
        reading
        for member in one.pass_ids
        for reading in rows.readings.get(member, ())
        if not reading.simulated
    ]
    return min(readings, key=_informativeness, default=None)


def _informativeness(reading: Reading) -> tuple[int, str]:
    rank = (
        OUTCOME_ORDER.index(reading.outcome)
        if reading.outcome in OUTCOME_ORDER
        else len(OUTCOME_ORDER)
    )
    return rank, reading.assignment_id


def _place(
    one: LabelledPass,
    report: Reading,
    geometry: PassGeometry,
    rows: FeatureRows,
    settled_at: datetime,
) -> _Heard:
    detected = report.first_detection_at
    placed = None
    if detected is not None and geometry.track and geometry.track.azimuth_deg:
        placed = track_at(
            geometry.track, (detected - geometry.track.start).total_seconds()
        )
    return _Heard(
        settled_at=settled_at,
        detection=placed,
        timing_error_s=None
        if detected is None
        else (detected - one.aos).total_seconds(),
        noise_dbfs=report.noise_floor_dbfs,
        noise_cell=_noise_cell(one, geometry, rows.longitudes),
    )


def _horizon(heard: Sequence[_Heard], geometry: PassGeometry) -> tuple[float, int]:
    """Share of the pass above the learned horizon, and the detections behind it."""
    by_sector: dict[int, list[float]] = {}
    for one in heard:
        if one.detection is not None:
            azimuth, elevation = one.detection
            by_sector.setdefault(sector(azimuth, HORIZON_SECTOR_DEG), []).append(
                elevation
            )
    track = geometry.track
    if track is None or not track.azimuth_deg:
        peak, _ = peak_and_sweep(geometry)
        cell = sector(peak, HORIZON_SECTOR_DEG)
        floor = _learned_floor(by_sector.get(cell, []))
        top = geometry.max_elevation_deg
        share = 0.0 if top <= 0.0 else min(max((top - floor) / top, 0.0), 1.0)
        return share, len(by_sector.get(cell, []))
    cells = [sector(one, HORIZON_SECTOR_DEG) for one in track.azimuth_deg]
    floors = {cell: _learned_floor(by_sector.get(cell, [])) for cell in set(cells)}
    above = sum(
        elevation > floors[cell]
        for cell, elevation in zip(cells, track.elevation_deg, strict=True)
    )
    return above / len(cells), sum(len(by_sector.get(cell, [])) for cell in floors)


def _learned_floor(elevations: Sequence[float]) -> float:
    """The sector's lower quartile of detection elevations, shrunk towards 0°."""
    if not elevations:
        return 0.0
    ordered = sorted(elevations)
    quartile = ordered[int(_LOWER_QUARTILE * (len(ordered) - 1))]
    return len(ordered) / (len(ordered) + SHRINK) * quartile


def _interference(heard: Sequence[_Heard], cell: tuple[int, int]) -> tuple[float, int]:
    """The cell's median noise over the station's, shrunk towards 0 dB."""
    readings = [one for one in heard if one.noise_dbfs is not None]
    in_cell = [one.noise_dbfs for one in readings if one.noise_cell == cell]
    if not in_cell:
        return 0.0, 0
    overall = median(one.noise_dbfs for one in readings if one.noise_dbfs is not None)
    lift = median(value for value in in_cell if value is not None) - overall
    return len(in_cell) / (len(in_cell) + SHRINK) * lift, len(in_cell)


def _noise_cell(
    one: LabelledPass, geometry: PassGeometry, longitudes: Mapping[str, float]
) -> tuple[int, int]:
    """The pass's peak sector, and its 4-hour band of local solar hour.

    Local solar hour is UTC plus longitude over 15, as the propensity's is
    (D-152); a station with no longitude in the snapshot is taken at 0°.
    """
    peak, _ = peak_and_sweep(geometry)
    longitude = longitudes.get(one.station_id, 0.0)
    hour = (one.aos.hour + one.aos.minute / 60.0 + longitude / 15.0) % 24.0
    return sector(peak, NOISE_SECTOR_DEG), int(hour // HOUR_BAND_H)


def _divergence(one: LabelledPass, rows: FeatureRows) -> float:
    """Seconds between the earliest and latest ``aos`` predicted before the pass."""
    rises = [
        held.aos
        for member in one.pass_ids
        if (held := rows.geometry.get(member)) is not None
        and held.computed_at <= one.aos
    ]
    if len(rises) < _A_SPREAD:
        return 0.0
    return (max(rises) - min(rises)).total_seconds()
