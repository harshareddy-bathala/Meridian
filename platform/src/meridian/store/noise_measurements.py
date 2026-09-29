"""Noise measurements — the floor each reception reported, as its own record.

Writes ``noise_measurements`` (``deploy/migrations/sql/0021_deferred_storage.sql``).
An observation that carries a noise floor gets one row here, written in the
same transaction as the observation revision it came from (D-173). The row is
derived from that stored revision, not from the submission, by the same query
migration 0021 backfilled every earlier observation with. There is therefore one
definition of an observation's floor, and a row written at ingest cannot differ
from one the backfill would have written.

What the row adds to the observation's two columns is where it was heard:
- the assignment's frequency, which is what the station was tuned to;
- the transmitter's bandwidth, where the catalogue knows it.

Its azimuth is null. One figure covers a whole pass, and the sector is assigned
where a profile is derived (D-159, D-173).

Reference: docs/DECISIONS.md D-103, D-173, D-178.
"""

from __future__ import annotations

from meridian.store.stations import Connection

__all__ = ["record_observation_floor"]

_RECORD_FLOOR = """
    insert into noise_measurements (
        station_id, measured_at, source, assignment_id, revision, centre_freq_hz,
        bandwidth_hz, noise_floor_dbfs, receiver_gain_db, simulated
    )
    select o.station_id, o.started_at, 'observation', o.assignment_id, o.revision,
           a.centre_freq_hz,
           case when t.bandwidth_hz > 0 then t.bandwidth_hz end,
           o.noise_floor_dbfs, o.receiver_gain_db, o.simulated
    from observations o
    join assignments a on a.assignment_id = o.assignment_id
    left join lateral (
        select bandwidth_hz
        from satellite_transmitters t
        where t.satellite_id = o.satellite_id
          and t.centre_freq_hz = a.centre_freq_hz
          and t.mode = a.mode
          and t.deleted_at is null
        order by t.id
        limit 1
    ) t on true
    where o.assignment_id = %(assignment_id)s
      and o.revision = %(revision)s
      and o.noise_floor_dbfs is not null
"""


def record_observation_floor(
    conn: Connection, *, assignment_id: str, revision: int
) -> bool:
    """Write the noise row for one stored observation revision, if it has a floor.

    Args:
        conn: An open connection, inside the transaction that wrote the
            revision, so the two rows commit or roll back together.
        assignment_id: The observation's assignment.
        revision: The revision just written.

    Returns:
        Whether a row was written. False for a revision that reported no floor,
        which is what every MSP 0.2 observation is.
    """
    with conn.cursor() as cur:
        cur.execute(
            _RECORD_FLOOR, {"assignment_id": assignment_id, "revision": revision}
        )
        return cur.rowcount == 1
