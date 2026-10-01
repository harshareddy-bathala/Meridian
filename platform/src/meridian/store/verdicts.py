"""Reception verdicts — what to score, and the rows that record each score.

Reads and writes ``reception_verdicts``
(``deploy/migrations/sql/0028_reception_verdicts.sql``).

**What is scored.** Every observation revision of a scheduled assignment whose
window has closed and which has no verdict by this method yet. These are the
receptions a raw snapshot asks the registry about (D-145), so the listening
answer the writer gets is the one a snapshot freezes. A verdict written now
and the same verdict recomputed from a snapshot then read the same inputs and
hash the same (D-261).

**What each row carries for the inputs.** The observation's own evidence, the
assignment's frequency, mode and window, the pass's satellite, acquisition and
loss, and the matching transmitter's frame interval. The interval comes from
the same transmitter :mod:`meridian.datasets.frames_expected` picks: a live row
before a withdrawn one, then the lowest id.

**Append-only.** An insert that meets an existing (assignment, revision,
method) does nothing, so a re-run writes nothing and two writers cannot
disagree about a row.

Reference: docs/DECISIONS.md D-104, D-145, D-250, D-261, D-263.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row

from meridian.store.stations import Connection

__all__ = ["NewVerdict", "UnscoredReception", "insert_verdict", "unscored_receptions"]

_UNSCORED = """
    select o.assignment_id, o.revision, o.started_at, o.station_id, o.simulated,
           o.outcome, o.signal_detected, o.peak_snr_db, o.frames_decoded,
           o.decoder, o.decoder_version,
           a.centre_freq_hz, a.mode, a.start_at, a.end_at,
           p.satellite_id, p.aos, p.los,
           (select t.frame_interval_s
              from satellite_transmitters t
             where t.satellite_id = p.satellite_id
               and t.centre_freq_hz = a.centre_freq_hz
               and t.mode = a.mode
             order by t.deleted_at is not null, t.id
             limit 1) as frame_interval_s
    from observations o
    join assignments a on a.assignment_id = o.assignment_id
    join passes p on p.id = a.pass_id
    where a.decision = 'scheduled'
      and a.end_at <= %(now)s
      and not exists (
          select 1 from reception_verdicts v
          where v.assignment_id = o.assignment_id
            and v.revision = o.revision
            and v.method = %(method)s)
    order by o.started_at, o.assignment_id, o.revision
    limit %(limit)s
"""

_INSERT = """
    insert into reception_verdicts (
        assignment_id, revision, observation_started_at, station_id,
        probability_usable, method, route, inputs_sha256, partial_below, simulated
    ) values (
        %(assignment_id)s, %(revision)s, %(observation_started_at)s, %(station_id)s,
        %(probability_usable)s, %(method)s, %(route)s, %(inputs_sha256)s,
        %(partial_below)s, %(simulated)s
    )
    on conflict do nothing
"""


@dataclass(frozen=True, slots=True)
class UnscoredReception:
    """One observation revision to score, with everything its inputs need."""

    assignment_id: str
    revision: int
    started_at: datetime
    station_id: str
    simulated: bool
    outcome: str
    signal_detected: bool
    peak_snr_db: float | None
    frames_decoded: int | None
    decoder: str | None
    decoder_version: str | None
    centre_freq_hz: int
    mode: str
    start_at: datetime
    end_at: datetime
    satellite_id: str
    aos: datetime
    los: datetime
    frame_interval_s: float | None


@dataclass(frozen=True, slots=True)
class NewVerdict:
    """One verdict row, as written."""

    assignment_id: str
    revision: int
    observation_started_at: datetime
    station_id: str
    probability_usable: float
    method: str
    route: str
    inputs_sha256: bytes
    partial_below: float
    simulated: bool


def unscored_receptions(
    conn: Connection, *, method: str, now: datetime, limit: int | None = None
) -> list[UnscoredReception]:
    """Every closed, scheduled reception with no verdict by ``method``, oldest first.

    Args:
        conn: An open connection.
        method: The verdict model's method.
        now: Windows ending at or before this have closed.
        limit: At most this many, the oldest; ``None`` for all.

    Returns:
        The receptions, with their inputs' raw material.
    """
    with conn.cursor(row_factory=class_row(UnscoredReception)) as cur:
        cur.execute(_UNSCORED, {"method": method, "now": now, "limit": limit})
        return cur.fetchall()


def insert_verdict(conn: Connection, verdict: NewVerdict) -> bool:
    """Write one verdict; nothing when the revision already has one by this method.

    Returns:
        Whether a row was written.
    """
    with conn.cursor() as cur:
        cur.execute(
            _INSERT,
            {
                "assignment_id": verdict.assignment_id,
                "revision": verdict.revision,
                "observation_started_at": verdict.observation_started_at,
                "station_id": verdict.station_id,
                "probability_usable": verdict.probability_usable,
                "method": verdict.method,
                "route": verdict.route,
                "inputs_sha256": verdict.inputs_sha256,
                "partial_below": verdict.partial_below,
                "simulated": verdict.simulated,
            },
        )
        return cur.rowcount > 0
