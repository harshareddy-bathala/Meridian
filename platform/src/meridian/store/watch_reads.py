"""What the receive-chain watch reads, and what is read of it.

**For the watch:** each heard reception, and its chain.

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

**Of the watch:** each chain of a station with its current baseline and its
latest warnings, for the public API; every station's warnings in one list,
newest first, for the network's view (D-331); and, for Prometheus, how many chains each
population has a baseline for and how many warnings are open (D-295).

Reference: docs/DECISIONS.md D-272, D-290, D-291, D-293, D-295, D-331.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import class_row

from meridian.store.receive_chain_warnings import StoredWarning
from meridian.store.signal_baselines import StoredBaseline, find_current_baseline
from meridian.store.stations import Connection

__all__ = [
    "ChainReception",
    "ChainWatch",
    "NetworkWarning",
    "WatchCount",
    "WatchUnder",
    "count_watched",
    "find_chain_receptions",
    "find_network_warnings",
    "find_station_watch",
]

EVIDENCE_PREFERRED = ("samples", "peak")
"""Which baseline a chain shows when it has both: MSP 0.3's, placed sample by
sample, before MSP 0.2's peaks."""


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


@dataclass(frozen=True, slots=True)
class WatchUnder:
    """The baseline and watch methods and configurations one deployment reads."""

    baseline_method: str
    baseline_sha256: bytes
    watch_method: str
    watch_sha256: bytes


@dataclass(frozen=True, slots=True)
class ChainWatch:
    """One receive chain of a station: its baseline, and its latest warnings."""

    capability_id: int
    band: str
    freq_min_hz: int
    freq_max_hz: int
    baseline: StoredBaseline | None
    warnings: list[StoredWarning]
    """Newest first, open or cleared."""


@dataclass(frozen=True, slots=True)
class _Capability:
    id: int
    band: str
    freq_min_hz: int
    freq_max_hz: int


def find_station_watch(
    conn: Connection, station_id: str, *, under: WatchUnder, latest: int = 5
) -> list[ChainWatch]:
    """Each live capability of the station, with its baseline and warnings.

    Args:
        conn: An open connection.
        station_id: The station.
        under: The methods and configurations the deployment watches under.
        latest: The most warnings to return per chain, newest first.
    """
    with conn.cursor(row_factory=class_row(_Capability)) as cur:
        cur.execute(
            "select id, band, freq_min_hz, freq_max_hz from station_capabilities"
            " where station_id = %s and deleted_at is null order by id",
            (station_id,),
        )
        capabilities = cur.fetchall()
    found = []
    for one in capabilities:
        baseline = next(
            (
                held
                for evidence in EVIDENCE_PREFERRED
                if (
                    held := find_current_baseline(
                        conn,
                        station_id=station_id,
                        capability_id=one.id,
                        evidence=evidence,
                        method=under.baseline_method,
                        config_sha256=under.baseline_sha256,
                    )
                )
                is not None
            ),
            None,
        )
        found.append(
            ChainWatch(
                capability_id=one.id,
                band=one.band,
                freq_min_hz=one.freq_min_hz,
                freq_max_hz=one.freq_max_hz,
                baseline=baseline,
                warnings=_latest_warnings(conn, station_id, one.id, under, latest),
            )
        )
    return found


def _latest_warnings(
    conn: Connection,
    station_id: str,
    capability_id: int,
    under: WatchUnder,
    latest: int,
) -> list[StoredWarning]:
    with conn.cursor(row_factory=class_row(StoredWarning)) as cur:
        cur.execute(
            "select id, station_id, capability_id, baseline_id, raised_at,"
            " shortfall_db, affected_bins_json, evidence_json, cleared_at,"
            " cleared_shortfall_db, method, config_sha256, simulated"
            " from receive_chain_warnings"
            " where station_id = %s and capability_id = %s"
            " and method = %s and config_sha256 = %s"
            " order by raised_at desc, id desc limit %s",
            (station_id, capability_id, under.watch_method, under.watch_sha256, latest),
        )
        return cur.fetchall()


@dataclass(frozen=True, slots=True)
class NetworkWarning:
    """One receive-chain warning, with the station and chain it was raised on."""

    warning: StoredWarning
    band: str
    freq_min_hz: int
    freq_max_hz: int


@dataclass(frozen=True, slots=True)
class _NetworkRow:
    id: int
    station_id: str
    capability_id: int
    baseline_id: int
    raised_at: datetime
    shortfall_db: float
    affected_bins_json: list[dict[str, object]]
    evidence_json: dict[str, object]
    cleared_at: datetime | None
    cleared_shortfall_db: float | None
    method: str
    config_sha256: bytes
    simulated: bool
    band: str
    freq_min_hz: int
    freq_max_hz: int


def find_network_warnings(
    conn: Connection,
    *,
    under: WatchUnder,
    open_only: bool,
    before_id: int | None,
    limit: int,
) -> list[NetworkWarning]:
    """One page of every station's warnings, the most recently raised first.

    Args:
        conn: An open connection. Read-only.
        under: The methods and configurations the deployment watches under;
            another configuration's warnings are its own record, not this one's.
        open_only: Only warnings not yet cleared.
        before_id: The last warning on the previous page, or ``None``.
        limit: The most rows to return; callers ask for one more than they need.

    Returns:
        Warnings in descending id order, which is the order they were raised
        in. A soft-deleted station's are excluded, as every public list
        excludes them.
    """
    with conn.cursor(row_factory=class_row(_NetworkRow)) as cur:
        cur.execute(
            """
            select w.id, w.station_id, w.capability_id, w.baseline_id, w.raised_at,
                   w.shortfall_db, w.affected_bins_json, w.evidence_json,
                   w.cleared_at, w.cleared_shortfall_db, w.method, w.config_sha256,
                   w.simulated, c.band, c.freq_min_hz, c.freq_max_hz
            from receive_chain_warnings w
            join station_capabilities c on c.id = w.capability_id
            join stations s on s.station_id = w.station_id
            where s.deleted_at is null
              and w.method = %(method)s and w.config_sha256 = %(sha)s
              and (not %(open_only)s or w.cleared_at is null)
              and (%(before)s::bigint is null or w.id < %(before)s)
            order by w.id desc
            limit %(limit)s
            """,
            {
                "method": under.watch_method,
                "sha": under.watch_sha256,
                "open_only": open_only,
                "before": before_id,
                "limit": limit,
            },
        )
        rows = cur.fetchall()
    return [
        NetworkWarning(
            warning=StoredWarning(
                id=row.id,
                station_id=row.station_id,
                capability_id=row.capability_id,
                baseline_id=row.baseline_id,
                raised_at=row.raised_at,
                shortfall_db=row.shortfall_db,
                affected_bins_json=row.affected_bins_json,
                evidence_json=row.evidence_json,
                cleared_at=row.cleared_at,
                cleared_shortfall_db=row.cleared_shortfall_db,
                method=row.method,
                config_sha256=row.config_sha256,
                simulated=row.simulated,
            ),
            band=row.band,
            freq_min_hz=row.freq_min_hz,
            freq_max_hz=row.freq_max_hz,
        )
        for row in rows
    ]


@dataclass(frozen=True, slots=True)
class WatchCount:
    """One population's chains with a baseline, and its warnings open."""

    simulated: bool
    chains: int
    open: int


def count_watched(conn: Connection, *, under: WatchUnder) -> list[WatchCount]:
    """Per population, chains with a baseline and warnings open, under ``under``.

    A population with no baseline is absent: its chains are not watched yet,
    which is not the same as watched and well (D-086).
    """
    with conn.cursor(row_factory=class_row(WatchCount)) as cur:
        cur.execute(
            """
            select b.simulated,
                   count(distinct (b.station_id, b.capability_id))::int as chains,
                   (select count(*)::int from receive_chain_warnings w
                    where w.cleared_at is null and w.simulated = b.simulated
                      and w.method = %(watch)s
                      and w.config_sha256 = %(watch_sha)s) as open
            from signal_baselines b
            where b.method = %(baseline)s and b.config_sha256 = %(baseline_sha)s
            group by b.simulated
            order by b.simulated
            """,
            {
                "watch": under.watch_method,
                "watch_sha": under.watch_sha256,
                "baseline": under.baseline_method,
                "baseline_sha": under.baseline_sha256,
            },
        )
        return cur.fetchall()
