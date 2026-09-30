"""Judge a run's fault ledger against what the platform stored.

The database half of ``meridian reliability faults``: reads, for each fault in
the ledger, the evidence :mod:`meridian.reliability.faults` judges it by, and,
given a Prometheus address, when ``StationOffline`` first fired. The judgement
itself is that module's and is pure; this one only fetches (D-192).

Reference: docs/DECISIONS.md D-189, D-192.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from meridian.reliability.fault_model import silenced_until
from meridian.reliability.faults import (
    FaultVerdict,
    Gathered,
    InjectedFault,
    PlatformEvidence,
    StationEvidence,
    StationWork,
    judge_gathered,
)
from meridian.store.fault_evidence import (
    find_first_heartbeat_after,
    find_first_round_after,
    find_heartbeat_times,
    find_pass_classes,
    find_reported,
    find_reported_misses_between,
    find_rounds_between,
    find_station_work,
)
from meridian.store.stations import Connection

__all__ = [
    "AlertAnswer",
    "AlertHistory",
    "check_faults",
    "first_rise",
    "gather_evidence",
    "prometheus_alert_history",
]

MARGIN = timedelta(minutes=5)
"""How far either side of a fault its evidence is read.

Wider than SC-5's ninety seconds, so the heartbeat before a fault and the first
one after it are always inside the window, whatever the cadence.
"""


@dataclass(frozen=True, slots=True)
class AlertAnswer:
    """When an alert first began firing inside a window, and whether it already was."""

    fired_at: datetime | None
    already_firing: bool
    """Firing when the window opened. The alert is summed over the fleet, so a
    firing inherited from another station's fault times nothing about this one."""


AlertHistory = Callable[[str, datetime, datetime], list[float]]
"""Every sampled instant, in epoch seconds, an alert was firing between two."""

STATION_OFFLINE_ALERT = "StationOffline"
PROMETHEUS_STEP_S = 5
PROMETHEUS_TIMEOUT_S = 10.0
PROMETHEUS_CHUNK = timedelta(hours=12)
"""One ``query_range`` at most: Prometheus refuses more than 11,000 points a
query, which at a five-second step is fifteen hours."""


def check_faults(
    conn: Connection,
    faults: Sequence[InjectedFault],
    *,
    now: datetime,
    alerts: AlertHistory | None = None,
) -> tuple[FaultVerdict, ...]:
    """Judge every fault, in ledger order.

    Args:
        conn: A connection to the platform's database.
        faults: The ledger's windows, from
            :func:`~meridian.reliability.faults.read_fault_ledger`.
        now: When the evidence is read; a window still open is judged up to it.
        alerts: Where to read when an alert fired, or ``None`` not to ask. Read
            once for the whole run, not once a fault: a seventy-two hour run
            has tens of thousands of windows.

    Raises:
        OSError: Prometheus could not be reached.
        ValueError: Prometheus answered with something that is not its API.
    """
    return tuple(
        judge_gathered(one)
        for one in gather_evidence(conn, faults, now=now, alerts=alerts)
    )


def gather_evidence(
    conn: Connection,
    faults: Sequence[InjectedFault],
    *,
    now: datetime,
    alerts: AlertHistory | None = None,
) -> tuple[Gathered, ...]:
    """Read every fault's evidence, in ledger order, without judging it.

    :func:`check_faults` judges what this returns; ``--publish`` keeps it, so
    the same verdicts can be reached from a sealed directory (D-240).

    Raises:
        OSError: Prometheus could not be reached.
        ValueError: Prometheus answered with something that is not its API.
    """
    by_target: dict[tuple[str, str], list[InjectedFault]] = {}
    for one in faults:
        by_target.setdefault((one.run_id, one.target), []).append(one)
    firing = _station_offline_history(faults, now, alerts)
    return tuple(
        _gather_platform(conn, one, now)
        if one.on_platform
        else _gather_station(
            conn,
            one,
            now,
            firing,
            tuple(x for x in by_target[(one.run_id, one.target)] if x is not one),
        )
        for one in faults
    )


def _station_offline_history(
    faults: Sequence[InjectedFault], now: datetime, alerts: AlertHistory | None
) -> list[float] | None:
    """When ``StationOffline`` was firing across the whole run, or ``None``."""
    stations = [one for one in faults if not one.on_platform]
    if alerts is None or not stations:
        return None
    start = min(one.opened_at for one in stations) - MARGIN
    end = min(max((one.closed_at or now) for one in stations) + MARGIN, now)
    return alerts(STATION_OFFLINE_ALERT, start, end)


def _gather_station(
    conn: Connection,
    fault: InjectedFault,
    now: datetime,
    firing: list[float] | None,
    alongside: tuple[InjectedFault, ...],
) -> Gathered:
    station_id = fault.station_id or ""
    start = fault.opened_at - MARGIN
    # To the end of every silencing fault overlapping this one, not its close
    # alone: recovery is judged from there, and its heartbeat must be in reach.
    quiet_until = silenced_until(fault, alongside) or fault.closed_at
    end = min(max(fault.closed_at or now, quiet_until or now) + MARGIN, now)
    work = tuple(
        StationWork(
            assignment_id=one.assignment_id,
            pass_id=one.pass_id,
            decided_at=one.decided_at,
            start_at=one.start_at,
            end_at=one.end_at,
            state=one.state,
            revoked_reason=one.revoked_reason,
            revoked_at=one.revoked_at,
            redecided_at=one.redecided_at,
            offline_revocations=tuple(one.offline_revocations),
            declined_at=one.declined_at,
            revocations=tuple(one.revocations),
            reinstatements=tuple(one.reinstatements),
        )
        for one in find_station_work(conn, station_id, start=start, end=end)
    )
    touched = sorted({one.assignment_id for one in work} | set(fault.assignment_ids))
    answer = (
        None
        if firing is None
        else first_rise(firing, fault.opened_at.timestamp(), end.timestamp())
    )
    rounds = find_rounds_between(conn, start=start, end=end)
    evidence = StationEvidence(
        heartbeats=find_heartbeat_times(conn, station_id, start=start, end=end),
        work=work,
        rounds=tuple(one.began for one in rounds),
        round_ends={one.began: one.wrote for one in rounds if one.wrote},
        classifications=find_pass_classes(conn, touched),
        reported=find_reported(conn, touched),
        as_of=now,
        alert_fired_at=answer.fired_at if answer else None,
        alert_already_firing=bool(answer and answer.already_firing),
        alert_asked=firing is not None,
    )
    return Gathered(fault, evidence, alongside)


def _gather_platform(conn: Connection, fault: InjectedFault, now: datetime) -> Gathered:
    closed = fault.closed_at
    evidence = PlatformEvidence(
        first_heartbeat_after=None
        if closed is None
        else find_first_heartbeat_after(conn, closed),
        first_round_after=None
        if closed is None
        else find_first_round_after(conn, closed),
        reported_misses=find_reported_misses_between(
            conn, start=fault.opened_at, end=closed or now
        ),
        as_of=now,
    )
    return Gathered(fault, evidence)


def prometheus_alert_history(base_url: str) -> AlertHistory:
    """An :data:`AlertHistory` reading a Prometheus server's ``ALERTS`` series.

    At a five-second step, finer than the fifteen-second evaluation interval
    the answer depends on, and in chunks of :data:`PROMETHEUS_CHUNK`.
    """

    def history(name: str, start: datetime, end: datetime) -> list[float]:
        instants: list[float] = []
        chunk_start = start
        while chunk_start < end:
            chunk_end = min(chunk_start + PROMETHEUS_CHUNK, end)
            instants.extend(_firing(base_url, name, chunk_start, chunk_end))
            chunk_start = chunk_end
        return sorted(set(instants))

    return history


def _firing(base_url: str, name: str, start: datetime, end: datetime) -> list[float]:
    """The instants in one range at which ``name`` was firing."""
    query = urllib.parse.urlencode(
        {
            "query": f'ALERTS{{alertname="{name}",alertstate="firing"}}',
            "start": f"{start.timestamp():.3f}",
            "end": f"{end.timestamp():.3f}",
            "step": str(PROMETHEUS_STEP_S),
        }
    )
    url = f"{base_url.rstrip('/')}/api/v1/query_range?{query}"
    with urllib.request.urlopen(url, timeout=PROMETHEUS_TIMEOUT_S) as response:
        body = json.load(response)
    return [
        float(stamp)
        for series in body.get("data", {}).get("result", [])
        for stamp, value in series.get("values", [])
        if value == "1"
    ]


def first_rise(
    instants: Sequence[float], start_s: float, end_s: float = float("inf")
) -> AlertAnswer:
    """The first firing sample in ``(start_s, end_s]``, unless it already was.

    Already firing means a sample in the step before ``start_s``: the alert
    was up when the fault began, for someone else.
    """
    if any(start_s - PROMETHEUS_STEP_S <= one <= start_s for one in instants):
        return AlertAnswer(fired_at=None, already_firing=True)
    after = [one for one in instants if start_s < one <= end_s]
    return AlertAnswer(
        fired_at=datetime.fromtimestamp(min(after), UTC) if after else None,
        already_firing=False,
    )
