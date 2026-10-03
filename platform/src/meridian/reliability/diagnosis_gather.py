"""A loss's evidence, gathered from Meridian's own records for the rules to read.

The rules (:mod:`~meridian.reliability.diagnosis`) see only plain values; this
is where those values come from. Everything is the station's own, or the
catalogue's, and as of the loss where a table keeps history (D-102, D-108):

* **Stage 20's classification of the pass**, which says whether the station was
  confirmed listening; it is read, never asked again (D-180, D-273);
* **other stations' attempts** at the satellite within the silence window, of
  the same population and reported by a station (D-276);
* **the floor** against the station's own median at the same gain (D-275);
* **the clock** in the station's heartbeats near the window (D-277);
* **the sky**: each SNR sample placed by the pass's own element set at the
  station's registered site, for this reception and for each earlier one the
  loss map reads (D-274). Computed here, for measured and simulated passes
  alike, because no stored track exists for a simulated pass (D-158).

Propagation needs no network: the orbit service runs on its bundled timescale.

Reference: docs/DECISIONS.md D-102, D-158, D-180, D-272 to D-277.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from meridian.orbit.service import OrbitService
from meridian.orbit.types import ElementSet, GroundSite
from meridian.registry import Registry
from meridian.reliability.config import DiagnosisConfig
from meridian.reliability.diagnosis_evidence import (
    HistoryPass,
    HorizonFloor,
    ListeningEvidence,
    LossEvidence,
    NoiseReading,
    ProfileCell,
    SatelliteCounts,
    SkySample,
    TimingEvidence,
)
from meridian.reliability.satellite_evidence import satellite_evidence
from meridian.store.diagnosis_reads import (
    HistoryRow,
    find_clock_traces,
    find_declared_floors,
    find_interference_cell,
    find_noise_baseline,
    find_site,
    find_station_history,
    find_transmitter_active,
)
from meridian.store.element_sets import find_element_set_by_id
from meridian.store.loss_diagnoses import DiagnosisSubject
from meridian.store.stations import Connection

__all__ = ["StationHistory", "gather", "place"]

LISTENING_SPAN = timedelta(days=1)
"""How far either side of its window an assignment's listening is looked for:
far past any clock error a station could still hold work under, and near enough
that the read touches a few chunks of heartbeats, not all of them."""

TRACK_STEP_S = 5.0
"""How finely a pass is propagated before each sample takes the nearest point."""


@dataclass
class StationHistory:
    """What one run has already placed, so a station's history is placed once."""

    sites: dict[str, GroundSite | None] = field(default_factory=dict)
    element_sets: dict[int, ElementSet] = field(default_factory=dict)
    placed: dict[str, tuple[SkySample, ...]] = field(default_factory=dict)
    rows: dict[str, list[HistoryRow]] = field(default_factory=dict)
    """Each station's earlier receptions read so far, in time order."""

    spans: dict[str, tuple[datetime, datetime]] = field(default_factory=dict)
    """The span each station's :attr:`rows` cover."""

    def receptions(
        self, conn: Connection, station_id: str, lookback: tuple[datetime, datetime]
    ) -> list[HistoryRow]:
        """The station's earlier receptions in ``lookback``, read once a run.

        A run takes losses oldest window first, so each later loss of a station
        asks for a little more than the last: only the new stretch is read.
        """
        since, until = lookback
        span = self.spans.get(station_id)
        if span is None or since < span[0]:
            self.rows[station_id] = find_station_history(
                conn, station_id=station_id, between=lookback
            )
            self.spans[station_id] = lookback
        elif until > span[1]:
            self.rows[station_id] += find_station_history(
                conn, station_id=station_id, between=(span[1], until)
            )
            self.spans[station_id] = (span[0], until)
        return [one for one in self.rows[station_id] if since <= one.started_at < until]


def gather(  # noqa: PLR0913 — the evidence's sources, each by name
    conn: Connection,
    registry: Registry,
    orbit: OrbitService,
    subject: DiagnosisSubject,
    *,
    config: DiagnosisConfig,
    history: StationHistory,
) -> LossEvidence:
    """Everything one diagnosis reads about one loss.

    Args:
        conn: An open connection, on which ``registry`` is also bound.
        registry: Answers whether each silent station was listening.
        orbit: Places samples in the station's sky.
        subject: The loss.
        config: The thresholds, for the windows each read spans.
        history: This run's cache of placed passes.

    Returns:
        The evidence.
    """
    site = _site(conn, subject.station_id, history)
    sky = (
        place(
            orbit,
            _element_set(conn, subject.element_set_id, history),
            site,
            subject.snr_samples or [],
        )
        if site is not None
        else ()
    )
    lookback = (
        subject.start_at - timedelta(seconds=config.lookback_s),
        subject.start_at,
    )
    return LossEvidence(
        listening=_listening(subject),
        satellite=_satellite(conn, registry, subject, config),
        noise=_noise(conn, subject, sky, site, lookback),
        timing=_timing(conn, subject, config),
        sky=sky,
        history=_history(
            conn, orbit, subject, site=site, lookback=lookback, history=history
        ),
        declared=tuple(
            HorizonFloor(one.azimuth_deg, one.azimuth_width_deg, one.min_elevation_deg)
            for one in find_declared_floors(
                conn, station_id=subject.station_id, at=subject.start_at
            )
        ),
    )


def place(
    orbit: OrbitService,
    element_set: ElementSet,
    site: GroundSite,
    samples: list[dict[str, object]],
) -> tuple[SkySample, ...]:
    """Each SNR sample, with the satellite's direction at its instant.

    A sample without a readable instant or value is left out, never guessed.
    """
    read = [
        (datetime.fromisoformat(str(one["t"]).replace("Z", "+00:00")), one["snr_db"])
        for one in samples
        if isinstance(one.get("t"), str) and isinstance(one.get("snr_db"), int | float)
    ]
    if not read:
        return ()
    first = min(t for t, _ in read)
    last = max(t for t, _ in read)
    angles = orbit.look_angles(
        element_set,
        site,
        first,
        last + timedelta(seconds=TRACK_STEP_S),
        step_s=TRACK_STEP_S,
    )
    if not angles:
        return ()
    placed = []
    for instant, snr in sorted(read, key=lambda one: one[0]):
        index = round((instant - first).total_seconds() / TRACK_STEP_S)
        angle = angles[min(max(index, 0), len(angles) - 1)]
        placed.append(
            SkySample(
                t=instant,
                snr_db=float(snr),  # type: ignore[arg-type]
                azimuth_deg=round(angle.azimuth_deg % 360.0, 2),
                elevation_deg=round(angle.elevation_deg, 2),
            )
        )
    return tuple(placed)


def _listening(subject: DiagnosisSubject) -> ListeningEvidence:
    evidence = subject.classification_evidence
    confirmed = evidence.get("listening_confirmed")
    return ListeningEvidence(
        classification_id=subject.classification_id,
        classification=subject.classification,
        outcome=subject.outcome,
        heard_during_window=bool(evidence.get("heard_during_window")),
        listening_confirmed=confirmed if isinstance(confirmed, bool) else None,
    )


def _satellite(
    conn: Connection,
    registry: Registry,
    subject: DiagnosisSubject,
    config: DiagnosisConfig,
) -> SatelliteCounts:
    window = timedelta(seconds=config.silent_window_s)
    signals, silences = satellite_evidence(
        conn,
        registry,
        satellite_id=subject.satellite_id,
        between=(subject.aos - window, subject.los + window),
        excluding=(subject.assignment_id,),
        simulated=subject.simulated,
        station_reported=True,
        silent_min_elevation_deg=config.silent_min_elevation_deg,
    )
    # The station's own other assignments of this pass are not another
    # station's attempt: their silence is this loss again, not a witness to it
    # (D-276). One that heard the satellite still counts, since it was heard.
    own = _pooled(subject)
    silences = [one for one in silences if one not in own]
    return SatelliteCounts(
        catalogue_active=find_transmitter_active(
            conn,
            satellite_id=subject.satellite_id,
            centre_freq_hz=subject.centre_freq_hz,
            mode=subject.mode,
        ),
        signals=len(signals),
        silences=len(silences),
        peak_elevation_deg=subject.max_elevation_deg,
    )


def _pooled(subject: DiagnosisSubject) -> frozenset[str]:
    """Every assignment Stage 20 pooled into this loss's physical pass."""
    held = subject.classification_evidence.get("assignments")
    return frozenset(
        str(one["assignment_id"])
        for one in (held if isinstance(held, list) else [])
        if isinstance(one, dict) and "assignment_id" in one
    )


def _noise(
    conn: Connection,
    subject: DiagnosisSubject,
    sky: tuple[SkySample, ...],
    site: GroundSite | None,
    lookback: tuple[datetime, datetime],
) -> NoiseReading:
    gain = subject.receiver_gain_db
    baseline, count = (
        find_noise_baseline(
            conn,
            station_id=subject.station_id,
            gain_db=gain,
            between=lookback,
            excluding=subject.assignment_id,
        )
        if gain is not None
        else (None, 0)
    )
    cell = None
    if sky and site is not None:
        peak = max(sky, key=lambda one: one.elevation_deg)
        aos = subject.aos
        hour = int((aos.hour + aos.minute / 60.0 + site.lon_deg / 15.0) % 24.0)
        found = find_interference_cell(
            conn,
            station_id=subject.station_id,
            at=subject.start_at,
            azimuth_deg=peak.azimuth_deg,
            hour=hour,
        )
        if found is not None:
            cell = ProfileCell(
                profile_id=found.id,
                azimuth_deg=found.azimuth_deg,
                azimuth_width_deg=found.azimuth_width_deg,
                hour_start=found.hour_start,
                hour_width=found.hour_width,
                noise_lift_db=found.noise_lift_db,
                sample_count=found.sample_count,
                gain_min_db=found.gain_min_db,
                gain_max_db=found.gain_max_db,
            )
    return NoiseReading(subject.noise_floor_dbfs, gain, baseline, count, cell)


def _timing(
    conn: Connection, subject: DiagnosisSubject, config: DiagnosisConfig
) -> TimingEvidence:
    margin = timedelta(seconds=config.clock_margin_s)
    traces = find_clock_traces(
        conn,
        station_id=subject.station_id,
        assignment_id=subject.assignment_id,
        between=(subject.start_at - margin, subject.end_at + margin),
        listening_between=(
            subject.start_at - LISTENING_SPAN,
            subject.end_at + LISTENING_SPAN,
        ),
    )
    recording = (
        (subject.observation_started_at, subject.observation_ended_at)
        if subject.observation_started_at is not None
        and subject.observation_ended_at is not None
        else None
    )
    return TimingEvidence(
        window=(subject.start_at, subject.end_at),
        uncertainty_s=subject.timing_uncertainty_s or 0.0,
        listening_span=traces.listening_span,
        skews_s=traces.skews_s,
        reported_offsets=traces.reported_offsets,
        recording=recording,
    )


def _history(  # noqa: PLR0913 — one station's past, placed once per run
    conn: Connection,
    orbit: OrbitService,
    subject: DiagnosisSubject,
    *,
    site: GroundSite | None,
    lookback: tuple[datetime, datetime],
    history: StationHistory,
) -> tuple[HistoryPass, ...]:
    if site is None:
        return ()
    passes = []
    for row in history.receptions(conn, subject.station_id, lookback):
        if row.assignment_id not in history.placed:
            history.placed[row.assignment_id] = place(
                orbit,
                _element_set(conn, row.element_set_id, history),
                site,
                row.snr_samples,
            )
        same_gain = row.receiver_gain_db == subject.receiver_gain_db
        passes.append(
            HistoryPass(
                samples=history.placed[row.assignment_id],
                noise_floor_dbfs=row.noise_floor_dbfs if same_gain else None,
            )
        )
    return tuple(passes)


def _site(
    conn: Connection, station_id: str, history: StationHistory
) -> GroundSite | None:
    if station_id not in history.sites:
        found = find_site(conn, station_id)
        history.sites[station_id] = (
            None
            if found is None
            else GroundSite(found.lat_deg, found.lon_deg, found.alt_m)
        )
    return history.sites[station_id]


def _element_set(
    conn: Connection, element_set_id: int, history: StationHistory
) -> ElementSet:
    if element_set_id not in history.element_sets:
        stored = find_element_set_by_id(conn, element_set_id)
        if stored is None:
            raise LookupError(f"element set {element_set_id} is gone")
        history.element_sets[element_set_id] = ElementSet(
            satellite_id=stored.satellite_id,
            epoch=stored.epoch,
            line1=stored.line1,
            line2=stored.line2,
            source=stored.source,
        )
    return history.element_sets[element_set_id]
