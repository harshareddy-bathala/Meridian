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

from meridian.reliability.faults import (
    FaultVerdict,
    InjectedFault,
    PlatformEvidence,
    StationEvidence,
    StationWork,
    judge_platform_fault,
    judge_station_fault,
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

__all__ = ["AlertAnswer", "AlertLookup", "check_faults", "prometheus_alert_lookup"]

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


AlertLookup = Callable[[str, datetime, datetime], AlertAnswer]
"""When an alert began firing between two instants."""

STATION_OFFLINE_ALERT = "StationOffline"
PROMETHEUS_STEP_S = 5
PROMETHEUS_TIMEOUT_S = 10.0


def check_faults(
    conn: Connection,
    faults: Sequence[InjectedFault],
    *,
    now: datetime,
    alerts: AlertLookup | None = None,
) -> tuple[FaultVerdict, ...]:
    """Judge every fault, in ledger order.

    Args:
        conn: A connection to the platform's database.
        faults: The ledger's windows, from
            :func:`~meridian.reliability.faults.read_fault_ledger`.
        now: When the evidence is read; a window still open is judged up to it.
        alerts: Where to ask when an alert fired, or ``None`` not to ask.
    """
    return tuple(
        _judge_platform(conn, one, now)
        if one.on_platform
        else _judge_station(conn, one, now, alerts, _same_target(one, faults))
        for one in faults
        if one.on_platform or one.station_id is not None
    )


def _same_target(
    fault: InjectedFault, faults: Sequence[InjectedFault]
) -> tuple[InjectedFault, ...]:
    """The other faults of the same run on the same station."""
    return tuple(
        one
        for one in faults
        if one is not fault
        and one.run_id == fault.run_id
        and one.target == fault.target
    )


def _judge_station(
    conn: Connection,
    fault: InjectedFault,
    now: datetime,
    alerts: AlertLookup | None,
    alongside: tuple[InjectedFault, ...],
) -> FaultVerdict:
    station_id = fault.station_id or ""
    start = fault.opened_at - MARGIN
    end = min((fault.closed_at or now) + MARGIN, now)
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
        None if alerts is None else alerts(STATION_OFFLINE_ALERT, fault.opened_at, end)
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
        alert_asked=alerts is not None,
    )
    return judge_station_fault(fault, evidence, alongside)


def _judge_platform(
    conn: Connection, fault: InjectedFault, now: datetime
) -> FaultVerdict:
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
    return judge_platform_fault(fault, evidence)


def prometheus_alert_lookup(base_url: str) -> AlertLookup:
    """An :data:`AlertLookup` asking a Prometheus server's ``ALERTS`` series.

    Queried over a range, at a five-second step, for the first sample at which
    the alert was firing. The step bounds the answer's resolution, which is
    finer than the fifteen-second evaluation interval the answer depends on.
    """

    def first_firing(name: str, start: datetime, end: datetime) -> AlertAnswer:
        # From a step before the window, to see whether it was already firing.
        query = urllib.parse.urlencode(
            {
                "query": f'ALERTS{{alertname="{name}",alertstate="firing"}}',
                "start": f"{start.timestamp() - PROMETHEUS_STEP_S:.3f}",
                "end": f"{end.timestamp():.3f}",
                "step": str(PROMETHEUS_STEP_S),
            }
        )
        url = f"{base_url.rstrip('/')}/api/v1/query_range?{query}"
        with urllib.request.urlopen(url, timeout=PROMETHEUS_TIMEOUT_S) as response:
            body = json.load(response)
        instants = sorted(
            float(stamp)
            for series in body.get("data", {}).get("result", [])
            for stamp, value in series.get("values", [])
            if value == "1"
        )
        return first_rise(instants, start.timestamp())

    return first_firing


def first_rise(instants: list[float], start_s: float) -> AlertAnswer:
    """The first firing sample after ``start_s``, unless it was firing already."""
    if any(one <= start_s for one in instants):
        return AlertAnswer(fired_at=None, already_firing=True)
    after = [one for one in instants if one > start_s]
    return AlertAnswer(
        fired_at=datetime.fromtimestamp(after[0], UTC) if after else None,
        already_firing=False,
    )
