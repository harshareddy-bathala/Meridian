"""Whether the platform handled an injected fault, judged from its own records.

A run's fault ledger says what was done and when (D-189). The platform never
reads it while it runs: it proves what it noticed from what it stored — the
heartbeats it received, the work it decided, revoked and decided again, and how
it classified each settled pass. This module joins the two afterwards and
answers, fault by fault, the roadmap's five questions (D-192):

``detected``
    Did the station read ``offline`` within SC-5's ninety seconds of the fault?
``no_new_work``
    Was it given nothing new while it was offline?
``replanned``
    Did a scheduling round that ran while it was offline revoke the work it had
    not begun, so the pass could be decided again?
``no_false_miss``
    Is no pass lost to the fault classified ``confirmed_miss``? Absence is not a
    miss (CLAUDE.md rule 7), and a fault is exactly when absence happens.
``recovered``
    Was the station heard again once the fault ended?

and two that only some faults ask: ``held`` (did the fault really stop the
station's heartbeats, or is the ledger claiming a silence the platform never
saw?) and ``declines_honoured`` (was each assignment a station let go of
revoked as declined?). With a Prometheus address, ``alerted`` reads when
``StationOffline`` first fired.

**Detection is derived, so its ninety seconds are arithmetic, and said so.**
Liveness is computed on read from the last heartbeat (D-054): a station silent
for ninety seconds *is* offline to every reader from that instant. What this
module checks is that the silence the ledger claims is the silence the platform
stored, and that everything downstream of liveness — the scheduler, the
classification — acted on it. The alert is the measured part of SC-5.

A check that does not apply to a fault, or cannot be judged from the evidence
yet, is ``None`` rather than a pass: a verdict counting it as passed would be
the platform grading itself on questions it was not asked.

Pure: the ledger arrives as lines, the evidence as values, and the store reads
are :mod:`meridian.store.fault_evidence`'s.

Reference: docs/DECISIONS.md D-054, D-171, D-181, D-189, D-190, D-192;
docs/PROJECT.md §9 SC-5.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from meridian.registry.liveness import OFFLINE_AFTER_S, derive_liveness

__all__ = [
    "LEDGER_VERSION",
    "Check",
    "FaultLedgerError",
    "FaultVerdict",
    "InjectedFault",
    "PlatformEvidence",
    "StationEvidence",
    "StationWork",
    "judge_platform_fault",
    "judge_station_fault",
    "read_fault_ledger",
]

LEDGER_VERSION = 1
"""The ledger format this module reads; ``meridian_sim/ledger.py`` defines it."""

OFFLINE = timedelta(seconds=OFFLINE_AFTER_S)

SILENCING = frozenset(
    {"network_down", "partition", "heartbeat_delayed", "token_revoked", "restart"}
)
"""Faults that stop a station's heartbeats reaching the platform."""

WHOLLY_SILENCING = SILENCING - {"restart"}
"""Faults under which no heartbeat may be stored at all.

A restart is an instant: the process that dies on one tick heartbeats on the
next, so a heartbeat inside its window is the station coming back, not the
fault failing to hold.
"""

MISS_SENSITIVE = SILENCING | {
    "upload_blocked",
    "receiver_down",
    "declines",
    "slow_api",
}
"""Faults under which a lost pass must never read as ``confirmed_miss``.

Each either removes the listening evidence, withholds the report, or declines
the work. A drifting clock and a failing decoder are left out: under both the
station did listen and heard what it heard, and what the classification makes
of that is Stages 25 and 27's question.
"""

PLATFORM_KINDS = frozenset(
    {"platform_restart", "database_restart", "scheduler_down", "api_paused"}
)

RECOVERY_WITHIN = OFFLINE
"""How soon after a fault ends a station must be heard again.

Three heartbeat intervals: a station coming back heartbeats on its next tick,
and one that has not been heard ninety seconds after its fault ended is, by
SC-5's own threshold, still down.
"""

ROUND_RECOVERY_WITHIN = timedelta(minutes=15)
"""How soon after the scheduler comes back a round must have run.

``ScheduledTaskStalled``'s threshold: the platform's own statement of how long
a round may take to happen before someone should be told.
"""


class FaultLedgerError(ValueError):
    """A ledger line this module cannot read as the format it expects."""


@dataclass(frozen=True, slots=True)
class InjectedFault:
    """One fault window from the ledger."""

    run_id: str
    kind: str
    target: str
    opened_at: datetime
    closed_at: datetime | None = None
    station_id: str | None = None
    assignment_ids: tuple[str, ...] = ()
    detail: Mapping[str, object] = field(default_factory=dict)

    @property
    def on_platform(self) -> bool:
        """Whether this fault was done to the platform rather than a station."""
        return self.target.startswith("platform:")


@dataclass(frozen=True, slots=True)
class StationWork:
    """One scheduled assignment of the station under a fault, as stored."""

    assignment_id: str
    pass_id: int
    decided_at: datetime
    start_at: datetime
    end_at: datetime
    state: str
    revoked_reason: str | None
    revoked_at: datetime | None
    redecided_at: datetime | None
    """When a later revision of the same pass was decided, if one was."""


@dataclass(frozen=True, slots=True)
class StationEvidence:
    """Everything the platform stored that bears on one station's fault."""

    heartbeats: tuple[datetime, ...]
    """Receipt instants, oldest first, over the fault and a margin either side."""

    work: tuple[StationWork, ...]
    rounds: tuple[datetime, ...]
    """When each scheduling round in the window judged liveness."""

    classifications: Mapping[str, str]
    """Stored class of each classified assignment, by assignment id."""

    as_of: datetime
    """When the evidence was read: nothing after it can have been stored."""

    alert_fired_at: datetime | None = None
    alert_asked: bool = False


@dataclass(frozen=True, slots=True)
class PlatformEvidence:
    """What bears on a fault done to the platform itself."""

    first_heartbeat_after: datetime | None
    """The first heartbeat from any station at or after the fault closed."""

    first_round_after: datetime | None
    """The first scheduling round at or after the fault closed."""

    confirmed_misses: tuple[str, ...]
    """Assignments classified ``confirmed_miss`` whose window met the fault."""

    as_of: datetime


@dataclass(frozen=True, slots=True)
class Check:
    """One question about one fault, and its answer."""

    name: str
    passed: bool | None
    """``None`` when the question does not apply, or cannot be answered yet."""

    detail: str


@dataclass(frozen=True, slots=True)
class FaultVerdict:
    """Every check for one fault window."""

    fault: InjectedFault
    checks: tuple[Check, ...]

    @property
    def passed(self) -> bool:
        """No check failed. A check that did not apply is not a failure."""
        return all(one.passed is not False for one in self.checks)


def read_fault_ledger(lines: Iterable[str]) -> tuple[InjectedFault, ...]:
    """Fold a ledger's lines into one fault per window, in the order they opened.

    Read independently of ``meridian_sim.ledger``, which the platform cannot
    import (D-138); ``tests/unit/test_reliability_faults.py`` reads a ledger the
    simulator wrote, so the two readers cannot drift apart unseen.

    Raises:
        FaultLedgerError: A line is not JSON, not this version, names an unknown
            event, or closes or acts on a window that is not open. Refused
            rather than skipped: a verdict against part of the truth is a
            verdict against a different run.
    """
    folding = _Folding()
    for number, raw in enumerate(lines, start=1):
        if raw.strip():
            folding.take(_parse_line(raw, number), number)
    return tuple(folding.faults)


class _Folding:
    """A ledger part-read: the windows so far, and which of them are open."""

    def __init__(self) -> None:
        self.faults: list[InjectedFault] = []
        self._open: dict[tuple[str, str, str], int] = {}

    def take(self, line: Mapping[str, object], number: int) -> None:
        """Apply one line to the windows read so far."""
        key = (
            _text(line, "run_id", number),
            _text(line, "target", number),
            _text(line, "kind", number),
        )
        at = _instant(_text(line, "at", number), number)
        event = line.get("event")
        if event == "open":
            self._opened(key, at, line, number)
        elif event in {"close", "act"}:
            if key not in self._open:
                raise FaultLedgerError(f"line {number}: {event} of an unopened {key}")
            index = self._open[key]
            if event == "close":
                self.faults[index] = replace(self.faults[index], closed_at=at)
                del self._open[key]
            else:
                self.faults[index] = replace(
                    self.faults[index],
                    assignment_ids=self.faults[index].assignment_ids
                    + _ids(line, number),
                )
        else:
            raise FaultLedgerError(f"line {number}: unknown event {event!r}")

    def _opened(
        self,
        key: tuple[str, str, str],
        at: datetime,
        line: Mapping[str, object],
        number: int,
    ) -> None:
        if key in self._open:
            raise FaultLedgerError(f"line {number}: {key} opened twice")
        self._open[key] = len(self.faults)
        station_id = line.get("station_id")
        detail = line.get("detail", {})
        self.faults.append(
            InjectedFault(
                run_id=key[0],
                target=key[1],
                kind=key[2],
                opened_at=at,
                station_id=station_id if isinstance(station_id, str) else None,
                detail=detail if isinstance(detail, dict) else {},
            )
        )


def _ids(line: Mapping[str, object], number: int) -> tuple[str, ...]:
    """The assignment ids an ``act`` line names."""
    ids = line.get("assignment_ids", [])
    if not isinstance(ids, list):
        raise FaultLedgerError(f"line {number}: assignment_ids is not a list")
    return tuple(str(one) for one in ids)


def judge_station_fault(
    fault: InjectedFault, evidence: StationEvidence
) -> FaultVerdict:
    """Answer every question that applies to a fault done to one station."""
    silence = _Silence.around(fault.opened_at, evidence.heartbeats)
    checks = [
        _held(fault, evidence),
        _detected(fault, silence),
        _no_new_work(silence, evidence),
        _replanned(silence, evidence),
        _no_false_miss(fault, evidence),
        _declines_honoured(fault, evidence),
        _recovered(fault, evidence),
    ]
    if evidence.alert_asked:
        checks.append(_alerted(fault, silence, evidence))
    return FaultVerdict(fault, tuple(checks))


def judge_platform_fault(
    fault: InjectedFault, evidence: PlatformEvidence
) -> FaultVerdict:
    """Answer the questions a fault done to the platform itself asks."""
    closed = fault.closed_at
    if closed is None:
        recovered = Check("recovered", None, "the fault never closed")
    elif fault.kind == "scheduler_down":
        recovered = _within(
            "scheduling round",
            closed,
            evidence.first_round_after,
            ROUND_RECOVERY_WITHIN,
            evidence.as_of,
        )
    else:
        recovered = _within(
            "heartbeat from any station",
            closed,
            evidence.first_heartbeat_after,
            RECOVERY_WITHIN,
            evidence.as_of,
        )
    misses = evidence.confirmed_misses
    no_false_miss = Check(
        "no_false_miss",
        not misses,
        "no pass during it is a confirmed miss"
        if not misses
        else f"confirmed misses during it: {', '.join(misses)}",
    )
    return FaultVerdict(fault, (no_false_miss, recovered))


@dataclass(frozen=True, slots=True)
class _Silence:
    """The gap in a station's heartbeats that a fault opened inside."""

    last: datetime | None
    """The last heartbeat at or before the fault opened."""

    next: datetime | None
    """The first heartbeat after it opened, or ``None`` if none was stored."""

    @classmethod
    def around(cls, opened_at: datetime, heartbeats: tuple[datetime, ...]) -> _Silence:
        before = [one for one in heartbeats if one <= opened_at]
        after = [one for one in heartbeats if one > opened_at]
        return cls(max(before, default=None), min(after, default=None))

    @property
    def offline_at(self) -> datetime | None:
        """When liveness first read ``offline`` in this gap, if it ever did."""
        if self.last is None:
            return None
        crossing = self.last + OFFLINE
        if self.next is not None and self.next < crossing:
            return None
        if derive_liveness(self.last, now=crossing) != "offline":
            return None
        return crossing


def _held(fault: InjectedFault, evidence: StationEvidence) -> Check:
    """Whether the platform stored the silence the ledger claims."""
    if fault.kind not in WHOLLY_SILENCING:
        return Check("held", None, f"{fault.kind} does not stop heartbeats")
    end = fault.closed_at or evidence.as_of
    inside = [one for one in evidence.heartbeats if fault.opened_at < one < end]
    return Check(
        "held",
        not inside,
        "no heartbeat was stored while it was in force"
        if not inside
        else f"{len(inside)} heartbeat(s) stored inside the window, first at "
        f"{inside[0].isoformat()}",
    )


def _detected(fault: InjectedFault, silence: _Silence) -> Check:
    """SC-5: offline within ninety seconds of the fault, when it lasted that long."""
    if fault.kind not in SILENCING:
        return Check("detected", None, f"{fault.kind} leaves the station reachable")
    if silence.last is None:
        return Check("detected", None, "the station was never heard before the fault")
    offline_at = silence.offline_at
    if offline_at is None:
        gap = (silence.next - silence.last).total_seconds() if silence.next else 0.0
        return Check(
            "detected",
            True,
            f"silent {gap:.0f} s, short of offline: never offline, correctly (D-190)",
        )
    latency = (offline_at - fault.opened_at).total_seconds()
    return Check(
        "detected",
        latency <= OFFLINE_AFTER_S,
        f"offline {latency:.0f} s after the fault began (SC-5: ≤ {OFFLINE_AFTER_S} s)",
    )


def _offline_span(silence: _Silence) -> tuple[datetime, datetime | None] | None:
    """From when the station was offline until it was heard again."""
    offline_at = silence.offline_at
    return None if offline_at is None else (offline_at, silence.next)


def _no_new_work(silence: _Silence, evidence: StationEvidence) -> Check:
    """Nothing decided for the station while it read offline (D-166)."""
    span = _offline_span(silence)
    if span is None:
        return Check("no_new_work", None, "the station was never offline")
    start, end = span
    given = [
        one.assignment_id
        for one in evidence.work
        if start <= one.decided_at and (end is None or one.decided_at < end)
    ]
    return Check(
        "no_new_work",
        not given,
        "nothing was decided for it while offline"
        if not given
        else f"decided while offline: {', '.join(given)}",
    )


def _replanned(silence: _Silence, evidence: StationEvidence) -> Check:
    """A round that ran while it was offline revoked the work it had not begun."""
    span = _offline_span(silence)
    if span is None:
        return Check("replanned", None, "the station was never offline")
    start, end = span
    during = [
        one for one in evidence.rounds if start <= one and (end is None or one < end)
    ]
    if not during:
        return Check("replanned", None, "no scheduling round ran while it was offline")
    first_round = min(during)
    owed = tuple(
        one
        for one in evidence.work
        if one.decided_at < start and one.start_at > first_round
    )
    return _revocations_of(owed, start)


def _revocations_of(owed: tuple[StationWork, ...], offline_at: datetime) -> Check:
    """Whether every piece of owed work was revoked because the station was offline."""
    kept = [
        one.assignment_id
        for one in owed
        if not (one.revoked_reason == "offline" and one.revoked_at is not None)
    ]
    if kept:
        return Check(
            "replanned", False, f"not revoked while offline: {', '.join(kept)}"
        )
    if not owed:
        return Check("replanned", True, "it held no unbegun work to revoke")
    revoked_at = min(one.revoked_at for one in owed if one.revoked_at is not None)
    decided_again = sum(one.redecided_at is not None for one in owed)
    return Check(
        "replanned",
        True,
        f"{len(owed)} revoked {(revoked_at - offline_at).total_seconds():.0f} s after "
        f"going offline; {decided_again} decided again",
    )


def _no_false_miss(fault: InjectedFault, evidence: StationEvidence) -> Check:
    """No pass the fault touched is a confirmed miss (CLAUDE.md rule 7)."""
    if fault.kind not in MISS_SENSITIVE:
        return Check("no_false_miss", None, f"{fault.kind} leaves listening intact")
    end = fault.closed_at or evidence.as_of
    touched = {
        one.assignment_id
        for one in evidence.work
        if one.start_at < end and one.end_at > fault.opened_at
    } | set(fault.assignment_ids)
    misses = sorted(
        one for one in touched if evidence.classifications.get(one) == "confirmed_miss"
    )
    classified = sum(one in evidence.classifications for one in touched)
    return Check(
        "no_false_miss",
        not misses,
        f"{classified} of {len(touched)} touched passes classified, none a miss"
        if not misses
        else f"confirmed misses: {', '.join(misses)}",
    )


def _declines_honoured(fault: InjectedFault, evidence: StationEvidence) -> Check:
    """Each assignment a declining station let go of was revoked as declined."""
    if fault.kind != "declines":
        return Check("declines_honoured", None, "not a decline")
    if not fault.assignment_ids:
        return Check("declines_honoured", None, "it let go of nothing")
    by_id = {one.assignment_id: one for one in evidence.work}
    wrong = [
        one
        for one in fault.assignment_ids
        if one in by_id and by_id[one].revoked_reason != "declined"
    ]
    return Check(
        "declines_honoured",
        not wrong,
        f"{len(fault.assignment_ids)} declined and revoked as declined"
        if not wrong
        else f"let go of and not revoked as declined: {', '.join(wrong)}",
    )


def _recovered(fault: InjectedFault, evidence: StationEvidence) -> Check:
    """Heard again once the fault ended."""
    closed = fault.closed_at
    if closed is None:
        return Check("recovered", None, "the fault never closed")
    after = min((one for one in evidence.heartbeats if one >= closed), default=None)
    return _within("heartbeat", closed, after, RECOVERY_WITHIN, evidence.as_of)


def _alerted(
    fault: InjectedFault, silence: _Silence, evidence: StationEvidence
) -> Check:
    """When ``StationOffline`` fired, measured from the fault — SC-5's measured half."""
    if _offline_span(silence) is None:
        # The alert is summed over the fleet, so one firing here may be another
        # station's; with no offline span of its own, this fault is owed none.
        return Check("alerted", None, "never offline, so no alert was owed")
    fired = evidence.alert_fired_at
    if fired is None:
        return Check("alerted", False, "StationOffline never fired")
    latency = (fired - fault.opened_at).total_seconds()
    return Check(
        "alerted", True, f"StationOffline fired {latency:.0f} s after the fault"
    )


def _within(
    what: str,
    since: datetime,
    seen: datetime | None,
    limit: timedelta,
    as_of: datetime,
) -> Check:
    """Whether ``what`` was seen within ``limit`` of ``since``."""
    if seen is None:
        if as_of < since + limit:
            return Check("recovered", None, f"no {what} yet, and too soon to tell")
        return Check("recovered", False, f"no {what} after it ended")
    late = (seen - since).total_seconds()
    return Check(
        "recovered",
        seen - since <= limit,
        f"first {what} {late:.0f} s after it ended",
    )


def _parse_line(raw: str, number: int) -> dict[str, object]:
    """One ledger line, parsed and checked to be this module's format."""
    try:
        line = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FaultLedgerError(f"line {number}: not JSON: {exc}") from exc
    if not isinstance(line, dict) or line.get("ledger") != LEDGER_VERSION:
        raise FaultLedgerError(f"line {number}: not a version {LEDGER_VERSION} line")
    return line


def _text(line: Mapping[str, object], name: str, number: int) -> str:
    value = line.get(name)
    if not isinstance(value, str):
        raise FaultLedgerError(f"line {number}: {name} is missing or not text")
    return value


def _instant(text: str, number: int) -> datetime:
    try:
        at = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FaultLedgerError(f"line {number}: bad instant {text!r}") from exc
    if at.tzinfo is None:
        raise FaultLedgerError(f"line {number}: instant {text!r} has no zone")
    return at
