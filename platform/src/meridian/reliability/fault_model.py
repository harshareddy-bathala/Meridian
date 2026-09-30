"""What a fault verdict is made of: its thresholds, its evidence and its answers.

The types :mod:`~meridian.reliability.faults` judges with, the kinds of fault
each question applies to, and the silence around a fault that the offline
questions start from. Pure; the evidence is read by
:mod:`~meridian.reliability.fault_check`.

Reference: docs/DECISIONS.md D-054, D-190, D-192.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from meridian.registry.liveness import OFFLINE_AFTER_S, derive_liveness
from meridian.reliability.fault_ledger import InjectedFault

__all__ = [
    "ALERT_VISIBLE_AFTER",
    "MISS_SENSITIVE",
    "NOT_LISTENED",
    "OFFLINE",
    "PLATFORM_KINDS",
    "RECOVERY_WITHIN",
    "ROUND_READ_GRACE",
    "ROUND_RECOVERY_WITHIN",
    "SILENCING",
    "WHOLLY_SILENCING",
    "Check",
    "FaultVerdict",
    "Gathered",
    "PlatformEvidence",
    "Silence",
    "StationEvidence",
    "StationWork",
    "offline_span",
    "silenced_until",
]

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

NOT_LISTENED = frozenset({"receiver_down", "declines"})
"""Faults whose ledger ``act`` lines name passes the station did not listen to."""

PLATFORM_KINDS = frozenset(
    {"platform_restart", "database_restart", "scheduler_down", "api_paused"}
)

ROUND_READ_GRACE = timedelta(seconds=60)
"""How long after it begins a round known only by its revocations may still be
reading liveness. A round with a run record says exactly, by when it wrote;
Stage 21's rehearsal measured ten seconds at ten stations."""

ALERT_VISIBLE_AFTER = timedelta(seconds=60)
"""How long a station must read offline before ``StationOffline`` can be owed.

The collector sees liveness at a scrape and the rule fires at the next
evaluation, each every fifteen seconds (``deploy/prometheus/prometheus.yml``),
so a spell shorter than both, with margin, can pass unseen by a correct
platform; the alert check does not ask about one.
"""

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

    offline_revocations: tuple[datetime, ...] = ()
    """Every instant it was revoked because its station was offline (D-196).

    From the revocation history, so a reinstatement since does not erase one.
    Each outage is judged by the revocations inside it: work revoked in an
    earlier outage and given back has not been revoked in this one.
    """

    declined_at: datetime | None = None
    """When it was first revoked as declined, from the same history."""

    revocations: tuple[datetime, ...] = ()
    """Every instant it was revoked, for any reason (D-196)."""

    reinstatements: tuple[datetime, ...] = ()
    """Every instant it was given back (D-171, D-196)."""

    def live_before(self, instant: datetime) -> bool:
        """Whether it was the station's work just before ``instant``.

        Not if it had been revoked and not given back since: a round cannot
        revoke what an earlier round already took, and owes it nothing.
        """
        taken = max((at for at in self.revocations if at < instant), default=None)
        if taken is None:
            return True
        back = max((at for at in self.reinstatements if at < instant), default=None)
        return back is not None and back > taken


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

    reported: frozenset[str]
    """The touched assignments of which a report is stored."""

    as_of: datetime
    """When the evidence was read: nothing after it can have been stored."""

    alert_fired_at: datetime | None = None
    alert_asked: bool = False
    alert_already_firing: bool = False
    """``StationOffline`` was firing for another station when this fault began."""

    round_ends: Mapping[datetime, datetime] = field(default_factory=dict)
    """When each round's reads were done, by when it began, where known."""


@dataclass(frozen=True, slots=True)
class PlatformEvidence:
    """What bears on a fault done to the platform itself."""

    first_heartbeat_after: datetime | None
    """The first heartbeat from any station at or after the fault closed."""

    first_round_after: datetime | None
    """The first scheduling round at or after the fault closed."""

    reported_misses: tuple[str, ...]
    """Passes whose window met the fault classified ``confirmed_miss`` although a
    report of them is stored."""

    as_of: datetime


@dataclass(frozen=True, slots=True)
class Check:
    """One question about one fault, and its answer."""

    name: str
    passed: bool | None
    """``None`` when the question does not apply, or cannot be answered yet."""

    detail: str
    latency_s: float | None = None
    """The interval the answer measured, as a number beside its sentence: for
    ``detected``, the fault's start to reading offline; for ``replanned``,
    reading offline to the first revocation; for ``alerted``, the fault's start
    to ``StationOffline`` firing. ``None`` where nothing was timed (D-240)."""


@dataclass(frozen=True, slots=True)
class FaultVerdict:
    """Every check for one fault window."""

    fault: InjectedFault
    checks: tuple[Check, ...]

    @property
    def passed(self) -> bool:
        """No check failed. A check that did not apply is not a failure."""
        return all(one.passed is not False for one in self.checks)


@dataclass(frozen=True, slots=True)
class Gathered:
    """One fault, and everything read to judge it — enough to judge it again.

    What ``meridian reliability faults --publish`` keeps, so a verdict can be
    reached again from a sealed directory with no database (D-240).
    """

    fault: InjectedFault
    evidence: StationEvidence | PlatformEvidence
    alongside: tuple[InjectedFault, ...] = ()
    """The other faults on the same station; recovery reads them."""


@dataclass(frozen=True, slots=True)
class Silence:
    """The gap in a station's heartbeats that a fault opened inside."""

    last: datetime | None
    """The last heartbeat at or before the fault opened."""

    next: datetime | None
    """The first heartbeat after it opened, or ``None`` if none was stored."""

    @classmethod
    def around(cls, opened_at: datetime, heartbeats: tuple[datetime, ...]) -> Silence:
        """The gap in ``heartbeats`` that a fault opening at ``opened_at`` is in."""
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


def offline_span(silence: Silence) -> tuple[datetime, datetime | None] | None:
    """From when the station was offline until it was heard again."""
    offline_at = silence.offline_at
    return None if offline_at is None else (offline_at, silence.next)


def silenced_until(
    fault: InjectedFault, alongside: Sequence[InjectedFault]
) -> datetime | None:
    """When the last silencing fault overlapping ``fault`` ended, or ``None``."""
    end = fault.closed_at
    others = [one for one in alongside if one.kind in SILENCING and one is not fault]
    grew = True
    while grew and end is not None:
        grew = False
        for one in others:
            if one.opened_at <= end and (one.closed_at is None or one.closed_at > end):
                if one.closed_at is None:
                    return None
                end, grew = one.closed_at, True
    return end
