"""Receptions as archives publish them — never rows in ``observations``.

Reads and writes ``archive_observations``
(``deploy/migrations/sql/0016_archive_ingest.sql``). A row here says that some
archive holds a reception: which station, which object, when, and what came of
it as that archive describes it.

**Its outcomes are not MSP outcomes.** ``no_data``, never ``no_signal``:
``no_signal`` asserts that a station was verifiably listening and heard nothing
(rule 7, D-010), and no heartbeat exists for somebody else's station. Different
values mean an accidental ``union`` of the two tables fails a CHECK instead of
returning a plausible number, and ``source_outcome`` keeps the archive's own
string so every mapping stays auditable (D-139).

**No foreign key to ``satellites``.** An FK would force this layer either to
drop receptions for objects we do not track — a second selection filter stacked
invisibly on the archive's own — or to insert into ``satellites``, letting an
external archive decide what pass generation propagates. So the key is text,
joined at read time, and :func:`count_satellite_coverage` reports the shortfall
as a number rather than applying it as a filter.

Reference: docs/DATA-MODEL.md; docs/DECISIONS.md D-139, D-140.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row

from meridian.store.stations import Connection

__all__ = [
    "ArchiveObservationArrival",
    "NewArchiveObservation",
    "NormalisationDisagreementError",
    "SatelliteCoverage",
    "StoredArchiveObservation",
    "count_satellite_coverage",
    "find_archive_observations_for_record",
    "insert_archive_observation",
]


class NormalisationDisagreementError(Exception):
    """One key, two different normalised bodies, from one normaliser version.

    Raised rather than absorbed. The whole stage rests on a snapshot
    renormalising to the same rows every time — that is the completion gate —
    so a normaliser that produced something different from the same bytes under
    the same version is a bug in the one property being demonstrated. Writing
    either row would settle the disagreement by whichever ran last.
    """


@dataclass(frozen=True, slots=True)
class NewArchiveObservation:
    """One archive reception in insertable form.

    ``loaded_at`` is absent: it is the platform's own clock and is left to the
    column default. ``started_at`` is present because it belongs to the
    reception rather than to us.
    """

    record_id: int
    source_id: str
    source_observation_id: str
    """The archive's own identifier for this reception."""

    transformation_version: str
    """The normaliser that produced this row.

    Part of the key, so re-normalising under a new version appends a second row
    instead of overwriting the first (D-140).
    """

    content_sha256: bytes
    """Of this row's canonical form, so a re-normalisation can be compared."""

    satellite_key: str
    """Canonical text, ``norad:NNNNN`` where the archive gave a catalogue
    number. Joined at read time; never a foreign key."""

    satellite_key_kind: str
    """``norad``, ``international_designator`` or ``source_name``.

    An unresolved identity is kept as what the archive actually said, rather
    than dropped or guessed into a catalogue number.
    """

    started_at: datetime
    archive_outcome: str
    """``decoded``, ``signal_no_decode``, ``no_data`` or ``unknown``."""

    archive_station_id: int | None = None
    ended_at: datetime | None = None
    max_elevation_deg: float | None = None
    centre_freq_hz: int | None = None
    mode: str | None = None
    source_outcome: str | None = None
    """The archive's own outcome string, verbatim, so the mapping is checkable."""

    peak_snr_db: float | None = None
    frames_decoded: int | None = None


@dataclass(frozen=True, slots=True)
class StoredArchiveObservation:
    """One row of ``archive_observations`` as read back."""

    archive_observation_id: int
    record_id: int
    source_id: str
    source_observation_id: str
    transformation_version: str
    content_sha256: bytes
    archive_station_id: int | None
    satellite_key: str
    satellite_key_kind: str
    started_at: datetime
    ended_at: datetime | None
    max_elevation_deg: float | None
    centre_freq_hz: int | None
    mode: str | None
    archive_outcome: str
    source_outcome: str | None
    peak_snr_db: float | None
    frames_decoded: int | None
    loaded_at: datetime


@dataclass(frozen=True, slots=True)
class ArchiveObservationArrival:
    """The stored id, and whether this call was what wrote it."""

    archive_observation_id: int
    written: bool


@dataclass(frozen=True, slots=True)
class SatelliteCoverage:
    """How many stored receptions name an object our catalogue knows.

    Reported, never applied. Filtering the unmatched away would stack a second
    selection filter on top of the archive's own — invisible downstream, and
    indistinguishable from the archive simply not holding those passes.
    """

    matched: int
    unmatched: int


@dataclass(frozen=True, slots=True)
class _ExistingRow:
    """The id and digest of a row an insert conflicted with."""

    archive_observation_id: int
    content_sha256: bytes


@dataclass(frozen=True, slots=True)
class _ObservationId:
    """One id, so it comes back typed rather than cast from ``object``."""

    archive_observation_id: int


def insert_archive_observation(
    conn: Connection, observation: NewArchiveObservation
) -> ArchiveObservationArrival:
    """Store one archive reception, or return the id it already has.

    Args:
        conn: An open connection.
        observation: The reception as the archive described it.

    Returns:
        The stored id, and whether this call wrote it.

    Raises:
        NormalisationDisagreementError: when this key is already stored under this
            transformation version with a *different* body.

    Note:
        Loading the same artefact twice writes nothing the second time, which
        is what lets the completion gate be demonstrated by simply running the
        load again.

        The disagreement check is the reason this is not a bare ``on conflict do
        nothing``. A conflict is only benign when the stored row says the same
        thing; when it does not, one of the two normalisations is wrong and
        neither the loader nor the table can tell which. Absorbing that would
        hide a bug in the single property this stage exists to demonstrate, so
        it stops instead.
    """
    with (
        conn.transaction(),
        conn.cursor(row_factory=class_row(_ObservationId)) as cur,
    ):
        cur.execute(
            """
            insert into archive_observations (record_id, source_id,
                source_observation_id, transformation_version, content_sha256,
                archive_station_id, satellite_key, satellite_key_kind,
                started_at, ended_at, max_elevation_deg, centre_freq_hz, mode,
                archive_outcome, source_outcome, peak_snr_db, frames_decoded)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s)
            on conflict on constraint archive_observation_unique do nothing
            returning archive_observation_id
            """,
            (
                observation.record_id,
                observation.source_id,
                observation.source_observation_id,
                observation.transformation_version,
                observation.content_sha256,
                observation.archive_station_id,
                observation.satellite_key,
                observation.satellite_key_kind,
                observation.started_at,
                observation.ended_at,
                observation.max_elevation_deg,
                observation.centre_freq_hz,
                observation.mode,
                observation.archive_outcome,
                observation.source_outcome,
                observation.peak_snr_db,
                observation.frames_decoded,
            ),
        )
        inserted = cur.fetchone()
        if inserted is not None:
            return ArchiveObservationArrival(
                archive_observation_id=inserted.archive_observation_id, written=True
            )
        return _existing(conn, observation)


def _existing(
    conn: Connection, observation: NewArchiveObservation
) -> ArchiveObservationArrival:
    """The row an insert conflicted with, once it is known to agree."""
    with conn.cursor(row_factory=class_row(_ExistingRow)) as cur:
        cur.execute(
            """
            select archive_observation_id, content_sha256
            from archive_observations
            where record_id = %s and source_observation_id = %s
              and transformation_version = %s
            """,
            (
                observation.record_id,
                observation.source_observation_id,
                observation.transformation_version,
            ),
        )
        stored = cur.fetchone()

    if stored is None:  # pragma: no cover — the conflict proves the row
        message = "insert conflicted but the conflicting row is not readable"
        raise RuntimeError(message)
    if stored.content_sha256 != observation.content_sha256:
        message = (
            f"{observation.source_id}/{observation.source_observation_id} already "
            f"stored under {observation.transformation_version} with a different "
            "body — the normaliser is not deterministic, so neither row can be "
            "trusted (docs/DECISIONS.md D-142)"
        )
        raise NormalisationDisagreementError(message)
    return ArchiveObservationArrival(
        archive_observation_id=stored.archive_observation_id, written=False
    )


def find_archive_observations_for_record(
    conn: Connection, record_id: int
) -> list[StoredArchiveObservation]:
    """Every reception normalised out of one artefact.

    Args:
        conn: An open connection. Read-only; opens no transaction of its own.
        record_id: The artefact to list.

    Returns:
        The receptions in reception order, empty when none was normalised.

    Note:
        Includes rows from every transformation version, because comparing two
        normalisations of one artefact is the reason the version is part of the
        key. Ordered by ``started_at`` and then by the archive's own identifier,
        so two runs list them identically even where a batch shares an instant.
    """
    with conn.cursor(row_factory=class_row(StoredArchiveObservation)) as cur:
        cur.execute(
            """
            select archive_observation_id, record_id, source_id,
                   source_observation_id, transformation_version, content_sha256,
                   archive_station_id, satellite_key, satellite_key_kind,
                   started_at, ended_at, max_elevation_deg, centre_freq_hz, mode,
                   archive_outcome, source_outcome, peak_snr_db, frames_decoded,
                   loaded_at
            from archive_observations
            where record_id = %s
            order by started_at asc, source_observation_id asc
            """,
            (record_id,),
        )
        return cur.fetchall()


def count_satellite_coverage(conn: Connection, source_id: str) -> SatelliteCoverage:
    """How many stored receptions name an object our catalogue knows.

    Args:
        conn: An open connection. Read-only; opens no transaction of its own.
        source_id: The source to count.

    Returns:
        The matched and unmatched counts. Both zero when nothing is stored.

    Note:
        A left join rather than an inner one, so the unmatched are counted
        instead of disappearing — which is the whole point. This number belongs
        beside any figure derived from these rows: an evaluation that silently
        omitted every object we do not track would report a coverage it never
        had, and nothing downstream could tell.
    """
    with conn.cursor(row_factory=class_row(SatelliteCoverage)) as cur:
        cur.execute(
            """
            select
                count(s.satellite_id) as matched,
                count(*) - count(s.satellite_id) as unmatched
            from archive_observations a
            left join satellites s on s.satellite_id = a.satellite_key
            where a.source_id = %s
            """,
            (source_id,),
        )
        counted = cur.fetchone()
    if counted is None:  # pragma: no cover — an aggregate always returns a row
        return SatelliteCoverage(matched=0, unmatched=0)
    return counted
