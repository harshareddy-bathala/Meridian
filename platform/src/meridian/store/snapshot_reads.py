"""Every table a raw snapshot holds, and the one query that reads each.

Stage 15's export reads these inside one ``REPEATABLE READ, READ ONLY``
transaction and writes each to its own JSON Lines file (D-143, D-144). This
module is only the reading: which rows, which columns, in which order.

**Rows are mappings here, not the frozen dataclasses the rest of this package
returns.** Nothing in the platform interprets a snapshot row — it is rendered to
bytes and hashed, and read back only by the labeller from the file. A dataclass
per table would be a third copy of each column list, beside the migration and
the ``select`` below, with nothing to check it against. The ``select`` is the
contract instead, and it names every column rather than ``select *``, so a
migration adding a column changes no snapshot until someone decides it should.

**Columns left out on purpose.** A station's ``token_sha256`` and
``registration_key_sha256`` are credentials. ``operator`` may name a person,
and no feature needs it. The token timestamps are current state that D-143
says no label may use. None of them is read, so none can reach a file.

**What a raw snapshot is not.** Coordinates are kept at full precision, because
Stage 17 computes geometry from them. That makes a raw snapshot private: it is
not published as-is, and Stage 30's evidence dataset decides what may be.

**Scope.** Passes whose ``aos`` falls in ``[since, as_of)``, and what they
depend on: their assignments, every observation revision submitted by
``as_of``, the heartbeats received inside each assignment's window, and the
element sets, stations, capabilities, satellites and transmitters they name.
Archive receptions are scoped by ``started_at`` over the same interval, and
bring the element sets current at each UTC day's start for every satellite
they name, so the export can compute an archive station's denominator (D-150). Nothing
outside the interval is read, so a pass near ``since`` has less contemporaneous
evidence than one in the middle — which the labeller reports as indeterminate,
not as a miss (D-147).

Reference: docs/DECISIONS.md D-139, D-143, D-144, D-145, D-150.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import dict_row

from meridian.store.stations import Connection

__all__ = [
    "SNAPSHOT_TABLES",
    "SnapshotScope",
    "SnapshotTable",
    "SourceTerms",
    "read_source_terms",
    "read_table",
    "snapshot_instant",
]


@dataclass(frozen=True, slots=True)
class SnapshotScope:
    """The interval a snapshot covers: ``since`` inclusive, ``as_of`` exclusive."""

    since: datetime
    as_of: datetime

    def __post_init__(self) -> None:
        """Refuse an interval that is naive or runs backwards."""
        if self.since.tzinfo is None or self.as_of.tzinfo is None:
            message = "a snapshot scope is timezone-aware"
            raise ValueError(message)
        if self.since > self.as_of:
            message = f"since {self.since} is after as_of {self.as_of}"
            raise ValueError(message)


@dataclass(frozen=True, slots=True)
class SnapshotTable:
    """One file in a raw snapshot, and the query that fills it."""

    name: str
    """The file is ``<name>.jsonl``."""

    sql: str
    """Takes ``%(since)s`` and ``%(as_of)s``; ends ``order by`` the primary key,
    so the same rows always render as the same bytes."""


_PASS_SCOPE = "los > %(since)s and aos < %(as_of)s"
"""Every prediction whose window reaches past ``since``, not only those rising
after it. Two predictions of one rise can fall either side of ``since`` by
seconds; reading both lets labelling see the rise whole and keep it or drop it
whole, rather than label half of it ``not_scheduled`` (D-148)."""

_SCOPED_PASSES = f"select id from passes where {_PASS_SCOPE}"
_SCOPED_ASSIGNMENTS = (
    f"select assignment_id from assignments where pass_id in ({_SCOPED_PASSES})"
)
_SCOPED_STATIONS = f"select station_id from passes where {_PASS_SCOPE}"
_SCOPED_SATELLITES = f"select satellite_id from passes where {_PASS_SCOPE}"
_SCOPED_ARCHIVE = (
    "select archive_observation_id from archive_observations"
    " where started_at >= %(since)s and started_at < %(as_of)s"
)
_CURRENT_FOR_ARCHIVE = (
    "select current.id"
    " from (select distinct satellite_key from archive_observations"
    "  where satellite_key_kind = 'norad'"
    "  and started_at >= %(since)s and started_at < %(as_of)s) received"
    " cross join generate_series("
    "  date_trunc('day', %(since)s::timestamptz, 'UTC'),"
    "  %(as_of)s::timestamptz, interval '1 day') as day (starts)"
    " cross join lateral ("
    "  select e.id from element_sets e"
    "  where e.satellite_id = received.satellite_key and e.epoch <= day.starts"
    "  order by e.epoch desc, e.retrieved_at desc, e.id desc limit 1) current"
)
"""For each satellite an archive station received in scope, the element set
current at the start of every UTC day in scope — the set pass generation would
have used (``find_element_set_current_at``), and what the export propagates an
archive station's denominator from (D-150)."""

SNAPSHOT_TABLES: tuple[SnapshotTable, ...] = (
    SnapshotTable(
        "passes",
        "select id, satellite_id, station_id, aos, los, max_elevation_deg,"
        " max_elevation_at, aos_azimuth_deg, los_azimuth_deg, element_set_id,"
        " min_elevation_deg, computed_at, simulated"
        f" from passes where {_PASS_SCOPE} order by id",
    ),
    SnapshotTable(
        "assignments",
        "select assignment_id, pass_id, station_id, issued_at, start_at, end_at,"
        " centre_freq_hz, mode, timing_uncertainty_s, predicted_yield, priority,"
        " decision, reason, model_config, score, conflicts_with_assignment_id,"
        " state, simulated"
        f" from assignments where pass_id in ({_SCOPED_PASSES})"
        " order by assignment_id",
    ),
    SnapshotTable(
        "observations",
        "select assignment_id, revision, observation_id, station_id, satellite_id,"
        " started_at, ended_at, outcome, signal_detected, first_detection_at,"
        " peak_snr_db, doppler_samples, products_json, client_notes, simulated,"
        " provenance, submitted_at, content_sha256, noise_floor_dbfs,"
        " receiver_gain_db, snr_samples, decoder, decoder_version, frames_decoded,"
        " frames_failed"
        f" from observations where assignment_id in ({_SCOPED_ASSIGNMENTS})"
        " and submitted_at <= %(as_of)s"
        " order by assignment_id, revision, started_at",
    ),
    SnapshotTable(
        "heartbeats",
        "select h.id, h.station_id, h.sent_at, h.received_at, h.state,"
        " h.held_assignments, h.listening_assignment_id, h.listening_satellite_id,"
        " h.listening_freq_hz, h.listening_mode, h.health_json, h.clock_offset_s,"
        " h.clock_uncertainty_s, h.simulated"
        " from heartbeats h where h.received_at <= %(as_of)s"
        # A lower bound the planner can use: without it the exists() below is
        # evaluated against every heartbeat ever received, decompressing old
        # chunks to do it. The earliest scoped window is the exact bound, and
        # an uncorrelated subquery lets TimescaleDB exclude chunks at run time.
        "  and h.received_at >= (select min(start_at) from assignments"
        f"   where pass_id in ({_SCOPED_PASSES}))"
        " and exists ("
        "  select 1 from assignments a"
        f"  where a.pass_id in ({_SCOPED_PASSES})"
        "  and a.station_id = h.station_id"
        "  and h.received_at between a.start_at and a.end_at)"
        " order by h.id, h.received_at",
    ),
    SnapshotTable(
        "element_sets",
        "select id, satellite_id, epoch, retrieved_at, line1, line2, source,"
        " content_sha256 from element_sets where id in ("
        "  select element_set_id from passes"
        f"  where {_PASS_SCOPE}"
        f"  union {_CURRENT_FOR_ARCHIVE})"
        " order by id",
    ),
    SnapshotTable(
        "stations",
        "select station_id, name, lat_deg, lon_deg, alt_m, registered_at,"
        " location_precision_decimals, simulated, simulator_run_id, seed,"
        " client_implementation, client_version, deleted_at"
        f" from stations where station_id in ({_SCOPED_STATIONS})"
        " order by station_id",
    ),
    SnapshotTable(
        "capabilities",
        "select id, station_id, band, freq_min_hz, freq_max_hz, modes,"
        " polarisation, tracking, min_elevation_deg, horizon_mask_json, deleted_at"
        f" from station_capabilities where station_id in ({_SCOPED_STATIONS})"
        " order by id",
    ),
    SnapshotTable(
        "satellites",
        "select satellite_id, name, orbital_regime, active, priority, deleted_at"
        f" from satellites where satellite_id in ({_SCOPED_SATELLITES})"
        " order by satellite_id",
    ),
    SnapshotTable(
        "transmitters",
        "select id, satellite_id, centre_freq_hz, mode, polarisation, bandwidth_hz,"
        " active, source, deleted_at"
        f" from satellite_transmitters where satellite_id in ({_SCOPED_SATELLITES})"
        " order by id",
    ),
    SnapshotTable(
        "archive_observations",
        "select archive_observation_id, record_id, source_id, source_observation_id,"
        " transformation_version, content_sha256, archive_station_id,"
        " satellite_key, satellite_key_kind, started_at, ended_at,"
        " max_elevation_deg, centre_freq_hz, mode, archive_outcome, source_outcome,"
        " peak_snr_db, frames_decoded"
        " from archive_observations"
        " where started_at >= %(since)s and started_at < %(as_of)s"
        " order by archive_observation_id",
    ),
    SnapshotTable(
        "archive_stations",
        "select archive_station_id, record_id, source_id, source_station_key, name,"
        " lat_deg, lon_deg, alt_m, capability_json, content_sha256,"
        " denominator_inputs"
        " from archive_stations where archive_station_id in ("
        "  select archive_station_id from archive_observations"
        f"  where archive_observation_id in ({_SCOPED_ARCHIVE}))"
        " order by archive_station_id",
    ),
    SnapshotTable(
        "ingest_records",
        "select record_id, source_id, original_identifier, source_version,"
        " payload_kind, retrieved_at, sha256, raw_path, media_type, byte_count,"
        " valid_from, valid_to, superseded_by"
        " from ingest_provenance where record_id in ("
        "  select record_id from archive_observations"
        f"  where archive_observation_id in ({_SCOPED_ARCHIVE}))"
        " order by record_id",
    ),
)
"""Every file a raw snapshot holds, in the order the export writes them."""


@dataclass(frozen=True, slots=True)
class SourceTerms:
    """One archive source a snapshot holds rows from, and the terms they came under."""

    source_id: str
    licence: str
    terms_url: str
    attribution_entry: str
    records: int
    """Artefacts from this source that the snapshot's archive receptions cite."""


def snapshot_instant(conn: Connection) -> datetime:
    """The transaction's own time, which is the snapshot's ``as_of`` (D-143).

    Args:
        conn: A connection inside the export's transaction.

    Returns:
        ``now()`` — which Postgres fixes at the start of the transaction, so it
        is the instant every table in the snapshot was read at.
    """
    with conn.cursor() as cur:
        cur.execute("select now()")
        row = cur.fetchone()
    if row is None:  # pragma: no cover — `select now()` always returns a row
        message = "select now() returned no row"
        raise RuntimeError(message)
    instant = row[0]
    if not isinstance(instant, datetime):  # pragma: no cover — timestamptz
        message = f"now() returned {type(instant).__name__}"
        raise TypeError(message)
    return instant


def read_table(
    conn: Connection, table: SnapshotTable, scope: SnapshotScope
) -> list[Mapping[str, object]]:
    """Every row of one snapshot table inside the scope, in primary-key order.

    Args:
        conn: A connection inside the export's transaction.
        table: Which table, from :data:`SNAPSHOT_TABLES`.
        scope: The snapshot's interval.

    Returns:
        One mapping per row, keyed by column name.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(table.sql, {"since": scope.since, "as_of": scope.as_of})
        return list(cur.fetchall())


def read_source_terms(conn: Connection, scope: SnapshotScope) -> list[SourceTerms]:
    """The terms of every archive source the snapshot holds receptions from.

    Args:
        conn: A connection inside the export's transaction.
        scope: The snapshot's interval.

    Returns:
        One entry per source, ordered by ``source_id``, for the manifest — so a
        dataset carries "were we allowed to use this" with it (D-134).
    """
    with conn.cursor() as cur:
        cur.execute(
            "select source_id, licence, terms_url, attribution_entry,"
            " count(*) from ingest_provenance where record_id in ("
            "  select record_id from archive_observations"
            f"  where archive_observation_id in ({_SCOPED_ARCHIVE}))"
            " group by source_id, licence, terms_url, attribution_entry"
            " order by source_id",
            {"since": scope.since, "as_of": scope.as_of},
        )
        return [_source_terms(row) for row in cur.fetchall()]


def _source_terms(row: tuple[object, ...]) -> SourceTerms:
    """One grouped row, with every column the view declares ``not null`` checked."""
    source_id, licence, terms_url, attribution_entry, records = row
    texts = (source_id, licence, terms_url, attribution_entry)
    if not all(isinstance(one, str) for one in texts) or not isinstance(records, int):
        message = f"ingest_provenance returned an unexpected row: {row!r}"
        raise TypeError(message)
    return SourceTerms(
        source_id=str(source_id),
        licence=str(licence),
        terms_url=str(terms_url),
        attribution_entry=str(attribution_entry),
        records=records,
    )
