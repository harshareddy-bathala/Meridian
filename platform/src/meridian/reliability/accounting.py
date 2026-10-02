"""Classify every settled, scheduled pass, and record what it was decided from.

This is the live half of D-180. It gathers each pass's evidence from the
database and hands it to :func:`~meridian.reliability.classification.classify`,
the same function the snapshot labeller calls, then stores the class with the
evidence in ``pass_classifications``. Every reliability figure is counted from
those rows, so each can be traced back to the assignments, the report and the
heartbeats behind it.

**The unit is a physical pass.** Scheduled assignments of one station and
satellite whose windows overlap are one reception and are pooled, as D-146
pools them. From D-165 on a rise has at most one scheduled assignment per
station, so this matters only for history written before it. A pass settles
once its pooled window has closed by the margin, never one assignment at a
time, and another station's reception of the satellite is counted once per
physical pass too, as the snapshot labeller counts it.

**Untaken work is swept first, in the same transaction.** A pass is classified
once and for all, so it must not be read while an assignment nobody took is
still ``issued``: that would turn a decline into a pass the station was not
listening for. The jobs service sweeps on its own too; this makes the answer
the same whether or not that sweep ran (D-183).

**A run can be bounded.** ``limit`` classifies the passes that closed first
and leaves the rest for the next run, so the first run over a long history
does not hold one transaction for all of it.

**Listening is the registry's answer, asked for every pooled assignment left
with the station.** A miss exists only on ``Registry.was_listening``'s word
(CLAUDE.md rule 7), and this module never looks at a heartbeat's listening
block itself. An assignment the platform revoked before its window was never
the station's work (D-171): it is kept in the evidence with its reason, and
neither its listening nor its window's heartbeats are read.

**The satellite is judged on our own receptions only** (D-182). An archive is
training input, never a runtime dependency.

Reference: docs/DECISIONS.md D-146, D-147, D-165, D-171, D-180, D-182.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from meridian.registry import Registry
from meridian.reliability.classification import (
    METHOD,
    PASS_CLASSES,
    PassClass,
    PassEvidence,
    classify,
)
from meridian.reliability.config import ClassificationConfig
from meridian.reliability.satellite_evidence import (
    informativeness,
    overlapping,
    question,
    satellite_evidence,
)
from meridian.reliability.satellite_silence import SatelliteState, judge_satellite
from meridian.store.assignment_expiry import expire_untaken_assignments
from meridian.store.pass_classifications import NewClassification, insert_classification
from meridian.store.reliability_evidence import (
    LatestReport,
    SettledAssignment,
    find_latest_reports,
    find_unclassified_settled,
    heard_during,
)
from meridian.store.stations import Connection

__all__ = ["AccountingReport", "classify_settled"]


@dataclass(frozen=True, slots=True)
class AccountingReport:
    """What one run classified."""

    settled_by: datetime
    """Passes whose windows closed before this instant were eligible."""

    classified: int
    """Physical passes classified by this run."""

    written: int
    """Rows stored; fewer than ``classified`` only if another run raced it."""

    by_class: dict[str, int] = field(default_factory=dict)
    """Passes classified by this run, per class, every class present."""

    expired: int = 0
    """Untaken assignments this run expired before classifying."""

    deferred: int = 0
    """Settled passes left for the next run by ``limit``."""


@dataclass(frozen=True, slots=True)
class _Pass:
    """The scheduled assignments pooled into one physical pass."""

    assignments: tuple[SettledAssignment, ...]

    @property
    def representative(self) -> SettledAssignment:
        """The lowest assignment id, as the stored row's key."""
        return min(self.assignments, key=lambda one: one.assignment_id)

    @property
    def assignment_ids(self) -> tuple[str, ...]:
        return tuple(sorted(one.assignment_id for one in self.assignments))

    @property
    def window_start(self) -> datetime:
        return min(one.start_at for one in self.assignments)

    @property
    def window_end(self) -> datetime:
        return max(one.end_at for one in self.assignments)


def classify_settled(
    conn: Connection,
    registry: Registry,
    *,
    now: datetime,
    config: ClassificationConfig,
    limit: int | None = None,
) -> AccountingReport:
    """Classify every scheduled pass that has settled and is not yet classified.

    Args:
        conn: An open connection, on which ``registry`` is also bound.
        registry: Answers whether the station was listening.
        now: The run's instant, timezone-aware UTC. A pass settles once its
            pooled window closed more than ``config.settle_margin_s`` before it.
        config: The margins classifications are made under.
        limit: At most this many passes, those that closed first; None for all.

    Returns:
        What was expired, classified and stored.
    """
    if limit is not None and limit < 1:
        raise ValueError(f"limit must be at least 1, not {limit}")
    expired = expire_untaken_assignments(conn, now=now)
    settled_by = now - timedelta(seconds=config.settle_margin_s)
    config_sha256 = config.sha256()
    pending = find_unclassified_settled(
        conn, settled_by=settled_by, method=METHOD, config_sha256=config_sha256
    )
    settled = sorted(
        (one for one in _pool(pending) if one.window_end < settled_by),
        key=lambda one: (one.window_end, one.assignment_ids),
    )
    chosen = settled if limit is None else settled[:limit]
    by_class: dict[str, int] = dict.fromkeys(PASS_CLASSES, 0)
    classified = written = 0
    for one in chosen:
        row = _classify(conn, registry, one, config)
        classified += 1
        by_class[row.classification] += 1
        written += insert_classification(
            conn,
            NewClassification(
                assignment_ids=one.assignment_ids,
                pass_id=one.representative.pass_id,
                station_id=one.representative.station_id,
                satellite_id=one.representative.satellite_id,
                window_start=one.window_start,
                window_end=one.window_end,
                classification=row.classification,
                evidence=row.evidence,
                method=METHOD,
                config_sha256=config_sha256,
                simulated=row.simulated,
            ),
        )
    return AccountingReport(
        settled_by=settled_by,
        classified=classified,
        written=written,
        by_class=by_class,
        expired=expired,
        deferred=len(settled) - len(chosen),
    )


def _pool(assignments: Sequence[SettledAssignment]) -> Iterator[_Pass]:
    """Group assignments of one station and satellite whose windows overlap."""
    for group in overlapping(assignments):
        yield _Pass(group)


@dataclass(frozen=True, slots=True)
class _Decided:
    classification: PassClass
    evidence: dict[str, object]
    simulated: bool


def _classify(
    conn: Connection,
    registry: Registry,
    one: _Pass,
    config: ClassificationConfig,
) -> _Decided:
    """Gather one pass's evidence, classify it, and keep what was read."""
    target = one.representative
    reports = find_latest_reports(conn, one.assignment_ids)
    report = min(
        reports.values(),
        key=lambda one: informativeness(one.outcome, one.assignment_id),
        default=None,
    )
    kept = [held for held in one.assignments if held.revoked_reason is None]
    listening = {
        held.assignment_id: registry.was_listening(question(held)) for held in kept
    }
    heard = heard_during(
        conn, target.station_id, [(held.start_at, held.end_at) for held in kept]
    )
    simulated = any(held.simulated for held in one.assignments) or any(
        held.simulated for held in reports.values()
    )
    satellite: dict[str, object] = {}
    window = timedelta(seconds=config.silent_window_s)

    def judge() -> SatelliteState:
        signals, silences = satellite_evidence(
            conn,
            registry,
            satellite_id=target.satellite_id,
            between=(target.aos - window, target.los + window),
            excluding=one.assignment_ids,
            simulated=simulated,
        )
        state = judge_satellite(
            signals=len(signals),
            silences=len(silences),
            min_silent_attempts=config.silent_min_attempts,
        )
        satellite.update(
            state=state,
            window_s=config.silent_window_s,
            signal_assignment_ids=signals,
            silence_assignment_ids=silences,
        )
        return state

    classification = classify(
        PassEvidence(
            outcome=None if report is None else report.outcome,
            assignment_states=tuple(held.state for held in kept),
            revoked_reasons=tuple(
                held.revoked_reason
                for held in one.assignments
                if held.revoked_reason is not None
            ),
            heard_during_window=heard,
            listening_confirmed=any(listening.values()),
        ),
        judge,
    )
    evidence: dict[str, object] = {
        "assignments": [
            {
                "assignment_id": held.assignment_id,
                "pass_id": held.pass_id,
                "state": held.state,
                "window": [held.start_at.isoformat(), held.end_at.isoformat()],
                "revoked_reason": held.revoked_reason,
                "listening_confirmed": listening.get(held.assignment_id),
            }
            for held in sorted(one.assignments, key=lambda held: held.assignment_id)
        ],
        "report": None if report is None else _report(report),
        "heard_during_window": heard,
        "listening_confirmed": any(listening.values()),
        "satellite": satellite or None,
        "parameters": config.parameters(),
    }
    return _Decided(classification, evidence, simulated)


def _report(report: LatestReport) -> dict[str, object]:
    return {
        "assignment_id": report.assignment_id,
        "observation_id": report.observation_id,
        "revision": report.revision,
        "outcome": report.outcome,
    }
