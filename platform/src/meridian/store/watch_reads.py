"""What the receive-chain watch reads: each heard reception, and its chain.

A receive chain is a capability: the one whose frequency range holds the
assignment's frequency, and which was not deleted when the window opened
(D-290). The read counts how many capabilities qualify, so the caller can leave
out a reception two of them could have made rather than guess which one did.

**Only receptions that heard the satellite**, a current report that decoded or
heard signal it could not decode. A pass that heard nothing carries no signal
strength to compare.

**A loss another cause explains is not the chain's.** A reception diagnosed as
an obstruction, interference or a silent satellite, under the deployment's
diagnosis method and configuration, is left out. One not yet diagnosed is read.

Every read is the station's own: a baseline is a station's past, never another
station's (D-291).

Reference: docs/DECISIONS.md D-272, D-290, D-291, D-293.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row

from meridian.store.stations import Connection

__all__ = ["ChainReception", "find_chain_receptions"]


@dataclass(frozen=True, slots=True)
class ChainReception:
    """One heard reception, with the receive chain it belongs to."""

    assignment_id: str
    station_id: str
    started_at: datetime
    end_at: datetime
    """The end of the assignment's window."""

    element_set_id: int
    max_elevation_deg: float
    peak_snr_db: float | None
    snr_samples: list[dict[str, object]] | None
    capability_id: int | None
    """The chain, or ``None`` when no capability, or more than one, covers the
    frequency."""

    chains: int
    """How many capabilities cover the frequency: one is a chain."""

    simulated: bool


_RECEPTIONS = """
    select o.assignment_id, o.station_id, o.started_at, a.end_at,
           p.element_set_id, p.max_elevation_deg, o.peak_snr_db,
           case when jsonb_typeof(o.snr_samples) = 'array'
                then o.snr_samples end as snr_samples,
           chain.capability_id, chain.chains, a.simulated
    from observations_current o
    join assignments a on a.assignment_id = o.assignment_id
    join passes p on p.id = a.pass_id
    cross join lateral (
        select min(c.id) as capability_id, count(*)::int as chains
        from station_capabilities c
        where c.station_id = o.station_id
          and a.centre_freq_hz between c.freq_min_hz and c.freq_max_hz
          and (c.deleted_at is null or c.deleted_at > a.start_at)
    ) chain
    where o.started_at >= %(since)s and o.started_at < %(until)s
      and o.outcome in ('decoded', 'signal_no_decode')
      and (%(station)s::text is null or o.station_id = %(station)s)
      and not exists (
          select 1 from loss_diagnoses d
          where d.assignment_id = o.assignment_id
            and d.revision = o.revision
            and d.method = %(diagnosis_method)s
            and d.config_sha256 = %(diagnosis_sha256)s
            and d.cause = any(%(excluded)s))
    order by o.station_id, o.started_at, o.assignment_id
"""


def find_chain_receptions(  # noqa: PLR0913 — one query's parameters, each by name
    conn: Connection,
    *,
    between: tuple[datetime, datetime],
    diagnosis_method: str,
    diagnosis_sha256: bytes,
    excluded_causes: tuple[str, ...],
    station_id: str | None = None,
) -> list[ChainReception]:
    """Every heard reception that began in ``between``, oldest first per station.

    Args:
        conn: An open connection.
        between: Receptions that began in ``[since, until)``.
        diagnosis_method: The diagnosis method whose causes are read.
        diagnosis_sha256: That diagnosis's configuration hash.
        excluded_causes: A reception diagnosed as one of these is left out.
        station_id: One station's, or every station's when ``None``.

    Returns:
        The receptions, by station and then in time order.
    """
    since, until = between
    with conn.cursor(row_factory=class_row(ChainReception)) as cur:
        cur.execute(
            _RECEPTIONS,
            {
                "since": since,
                "until": until,
                "station": station_id,
                "diagnosis_method": diagnosis_method,
                "diagnosis_sha256": diagnosis_sha256,
                "excluded": list(excluded_causes),
            },
        )
        return cur.fetchall()
