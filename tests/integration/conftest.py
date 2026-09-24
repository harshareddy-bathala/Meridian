"""Row graphs the public schedule endpoints read, built once for all of them.

A pass needs a station, a satellite and an element set; an assignment needs a
pass; an observation needs an assignment. Each public read test wants a few of
each with chosen times, and building that chain by hand in every module is how
three copies drift apart.

``schedule_rows`` writes through the requesting module's ``rollback`` fixture, so
everything it inserts disappears with the test.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

LINE1 = "1 25544U 98067A   26226.50000000  .00001234  00000-0  12345-4 0  9991"
LINE2 = "2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.50377579123456"


@dataclass
class ScheduleRows:
    """Inserts the rows a pass, assignment or observation depends on."""

    conn: Any
    _hashes: itertools.count[int] = field(default_factory=lambda: itertools.count(1))

    def station(
        self, station_id: str, *, simulated: bool = True, deleted: bool = False
    ) -> str:
        """One station with unique credential hashes."""
        token, key = next(self._hashes), next(self._hashes)
        with self.conn.cursor() as cur:
            cur.execute(
                "insert into stations (station_id, name, operator, lat_deg, lon_deg,"
                " alt_m, token_sha256, registration_key_sha256, simulated,"
                " simulator_run_id, seed, deleted_at)"
                " values (%s, %s, 'tests', 12.97, 77.59, 920, %s, %s, %s, %s, %s, %s)",
                (
                    station_id,
                    f"name-{station_id}",
                    bytes([token % 256]) * 32,
                    bytes([key % 256]) * 32,
                    simulated,
                    "run-a" if simulated else None,
                    4471 if simulated else None,
                    datetime(2026, 1, 1, tzinfo=UTC) if deleted else None,
                ),
            )
        return station_id

    def satellite(self, satellite_id: str = "norad:99970") -> int:
        """One satellite and one element set for it; returns the element set id."""
        with self.conn.cursor() as cur:
            cur.execute(
                "insert into satellites (satellite_id, name) values (%s, 'Test')"
                " on conflict do nothing",
                (satellite_id,),
            )
            cur.execute(
                "insert into element_sets (satellite_id, epoch, line1, line2,"
                " source) values (%s, now() - interval '6 hours', %s, %s, 'manual')"
                " returning id",
                (satellite_id, LINE1, LINE2),
            )
            return int(cur.fetchone()[0])

    def pass_(
        self,
        station_id: str,
        aos: datetime,
        *,
        element_set_id: int,
        minutes: float = 11.0,
    ) -> int:
        """One pass of the default satellite, peaking at 61.4°; returns its id."""
        los = aos + timedelta(minutes=minutes)
        with self.conn.cursor() as cur:
            cur.execute(
                "insert into passes (satellite_id, station_id, aos, los,"
                " max_elevation_deg, max_elevation_at, aos_azimuth_deg,"
                " los_azimuth_deg, element_set_id, min_elevation_deg, simulated)"
                " values (%s, %s, %s, %s, %s, %s, 12.6, 201.3, %s, 10.0,"
                " (select simulated from stations where station_id = %s))"
                " returning id",
                (
                    "norad:99970",
                    station_id,
                    aos,
                    los,
                    61.4,
                    aos + (los - aos) / 2,
                    element_set_id,
                    station_id,
                ),
            )
            return int(cur.fetchone()[0])

    def assignment(self, assignment_id: str, pass_id: int, **columns: Any) -> str:
        """One assignment over a pass's own window and station."""
        values = {
            "decision": "scheduled",
            "reason": "highest elevation in its window",
            "state": "issued",
            "score": 0.61,
            "model_config": "A",
            "conflicts_with_assignment_id": None,
        } | columns
        with self.conn.cursor() as cur:
            cur.execute(
                "insert into assignments (assignment_id, pass_id, station_id, start_at,"
                " end_at, centre_freq_hz, mode, timing_uncertainty_s, priority,"
                " decision, reason, state, score, model_config,"
                " conflicts_with_assignment_id, simulated)"
                " select %s, id, station_id, aos, los, 137900000, 'lrpt', 4.2, 1.0,"
                " %s, %s, %s, %s, %s, %s, simulated from passes where id = %s",
                (
                    assignment_id,
                    values["decision"],
                    values["reason"],
                    values["state"],
                    values["score"],
                    values["model_config"],
                    values["conflicts_with_assignment_id"],
                    pass_id,
                ),
            )
        return assignment_id

    def observation(
        self, assignment_id: str, *, revision: int = 1, outcome: str = "decoded"
    ) -> None:
        """One observation revision for an assignment, over its window."""
        detected = outcome in {"decoded", "signal_no_decode"}
        with self.conn.cursor() as cur:
            cur.execute(
                "insert into observations (assignment_id, revision, started_at,"
                " ended_at, station_id, satellite_id, outcome, signal_detected,"
                " first_detection_at, peak_snr_db, content_sha256, simulated,"
                " client_notes)"
                " select a.assignment_id, %s, a.start_at, a.end_at, a.station_id,"
                " p.satellite_id, %s, %s,"
                " case when %s then a.start_at + interval '40 seconds' end,"
                " 11.5, %s, a.simulated, 'private note'"
                " from assignments a join passes p on p.id = a.pass_id"
                " where a.assignment_id = %s",
                (
                    revision,
                    outcome,
                    detected,
                    detected,
                    bytes([revision]) * 32,
                    assignment_id,
                ),
            )

    def element_set_at(self, satellite_id: str, epoch: datetime, source: str) -> int:
        """One more element set with a chosen epoch; ``source`` keeps it distinct.

        The lines are :data:`LINE1` and :data:`LINE2` every time, so two sets of
        one satellite differ only in provenance — which the content key allows.
        """
        with self.conn.cursor() as cur:
            cur.execute(
                "insert into satellites (satellite_id, name) values (%s, 'Test')"
                " on conflict do nothing",
                (satellite_id,),
            )
            cur.execute(
                "insert into element_sets (satellite_id, epoch, line1, line2,"
                " source) values (%s, %s, %s, %s, %s) returning id",
                (satellite_id, epoch, LINE1, LINE2, source),
            )
            return int(cur.fetchone()[0])

    def archive_reception(
        self,
        satellite_key: str,
        started_at: datetime,
        *,
        location: tuple[float, float] | None = (12.9, 77.6),
    ) -> int:
        """One archive reception by its own archive station; returns the station id.

        The source and artefact are shared by every call; each reception gets a
        station of its own, published at ``location`` or with none.
        """
        n = next(self._hashes)
        lat, lon = location if location is not None else (None, None)
        with self.conn.cursor() as cur:
            cur.execute(
                "insert into ingest_sources (source_id, source_class, name,"
                " licence, terms_url, access_constraint, attribution_entry)"
                " values ('reference_archive', 'archive_receptions',"
                " 'Reference archive', 'CC-BY-4.0', 'https://example.invalid/terms',"
                " 'none', 'The reference adapter''s fixtures are ours')"
                " on conflict do nothing"
            )
            cur.execute(
                "insert into ingest_records (source_id, original_identifier,"
                " source_version, payload_kind, retrieved_at, sha256, raw_path,"
                " media_type, byte_count) values ('reference_archive', %s, 'v1',"
                " 'data', %s, %s, %s, 'application/json', 128) returning record_id",
                (f"art-{n}", started_at, bytes([n % 256]) * 32, f"ref/{n}"),
            )
            record = int(cur.fetchone()[0])
            cur.execute(
                "insert into archive_stations (record_id, source_id,"
                " source_station_key, content_sha256, lat_deg, lon_deg)"
                " values (%s, 'reference_archive', %s, %s, %s, %s)"
                " returning archive_station_id",
                (record, f"gs-{n}", bytes([n % 256]) * 32, lat, lon),
            )
            station = int(cur.fetchone()[0])
            cur.execute(
                "insert into archive_observations (record_id, source_id,"
                " source_observation_id, transformation_version, content_sha256,"
                " archive_station_id, satellite_key, satellite_key_kind,"
                " started_at, archive_outcome) values (%s, 'reference_archive',"
                " %s, 'reference-1', %s, %s, %s, 'norad', %s, 'decoded')",
                (
                    record,
                    f"obs-{n}",
                    bytes([n % 256]) * 32,
                    station,
                    satellite_key,
                    started_at,
                ),
            )
        return station


@pytest.fixture
def schedule_rows(rollback: Any) -> ScheduleRows:
    """A row-graph builder writing through this test's rolled-back transaction."""
    return ScheduleRows(rollback)
