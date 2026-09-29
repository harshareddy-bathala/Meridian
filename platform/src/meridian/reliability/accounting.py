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

**Listening is the registry's answer, asked for every pooled assignment.** A
miss exists only on ``Registry.was_listening``'s word (CLAUDE.md rule 7), and
this module never looks at a heartbeat's listening block itself.

**The satellite is judged on our own receptions only** (D-182). An archive is
training input, never a runtime dependency.

Reference: docs/DECISIONS.md D-146, D-147, D-165, D-180, D-182.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol, TypeVar

from meridian.registry import ListeningQuery, Registry
from meridian.reliability.classification import (
    METHOD,
    OUTCOME_ORDER,
    PASS_CLASSES,
    PassClass,
    PassEvidence,
    classify,
)
from meridian.reliability.config import ClassificationConfig
from meridian.reliability.satellite_silence import (
    SIGNAL,
    SatelliteState,
    judge_satellite,
)
from meridian.store.assignment_expiry import expire_untaken_assignments
from meridian.store.pass_classifications import NewClassification, insert_classification
from meridian.store.reliability_evidence import (
    LatestReport,
    NearbyReception,
    SettledAssignment,
    find_latest_reports,
    find_receptions_near,
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


class _Windowed(Protocol):
    @property
    def assignment_id(self) -> str: ...
    @property
    def station_id(self) -> str: ...
    @property
    def satellite_id(self) -> str: ...
    @property
    def start_at(self) -> datetime: ...
    @property
    def end_at(self) -> datetime: ...


W = TypeVar("W", bound=_Windowed)


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
    for group in _overlapping(assignments):
        yield _Pass(group)


def _overlapping(held: Iterable[W]) -> Iterator[tuple[W, ...]]:
    """Assignments of one station and satellite whose windows overlap, grouped.

    Ordered by station, satellite and start, a group is extended while the next
    assignment begins before the group's end.
    """
    group: list[W] = []
    for one in sorted(
        held,
        key=lambda one: (
            one.station_id,
            one.satellite_id,
            one.start_at,
            one.assignment_id,
        ),
    ):
        if group and (
            (one.station_id, one.satellite_id)
            != (group[0].station_id, group[0].satellite_id)
            or one.start_at >= max(held.end_at for held in group)
        ):
            yield tuple(group)
            group = []
        group.append(one)
    if group:
        yield tuple(group)


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
        key=lambda one: _informativeness(one.outcome, one.assignment_id),
        default=None,
    )
    listening = {
        held.assignment_id: registry.was_listening(_question(held))
        for held in one.assignments
    }
    heard = heard_during(
        conn,
        target.station_id,
        [(held.start_at, held.end_at) for held in one.assignments],
    )
    simulated = any(held.simulated for held in one.assignments) or any(
        held.simulated for held in reports.values()
    )
    satellite: dict[str, object] = {}

    def judge() -> SatelliteState:
        signals, silences = _satellite_evidence(conn, registry, one, simulated, config)
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
            assignment_states=tuple(held.state for held in one.assignments),
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
                "listening_confirmed": listening[held.assignment_id],
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


def _satellite_evidence(
    conn: Connection,
    registry: Registry,
    one: _Pass,
    simulated: bool,
    config: ClassificationConfig,
) -> tuple[list[str], list[str]]:
    """Other receptions of the satellite near the pass, as D-147 counts them.

    Only receptions of the pass's own population count: a simulated reception
    is never evidence about a measured pass, nor the other way round. Each
    other physical pass counts once, by its most informative report, as the
    snapshot labeller counts it; one that heard nothing counts as a silence
    only if the registry confirms the station was listening for any of its
    assignments, as for the pass itself.

    Returns:
        The reporting assignment of each physical pass that heard a signal,
        and of each that heard nothing while confirmed listening.
    """
    target = one.representative
    window = timedelta(seconds=config.silent_window_s)
    nearby = [
        reception
        for reception in find_receptions_near(
            conn,
            satellite_id=target.satellite_id,
            between=(target.aos - window, target.los + window),
            excluding=one.assignment_ids,
        )
        if reception.simulated == simulated
    ]
    signals: list[str] = []
    silences: list[str] = []
    for physical in _overlapping(nearby):
        best = min(
            physical,
            key=lambda held: _informativeness(held.outcome, held.assignment_id),
        )
        if best.outcome in SIGNAL:
            signals.append(best.assignment_id)
        elif best.outcome == "no_signal" and any(
            registry.was_listening(_question(held)) for held in physical
        ):
            silences.append(best.assignment_id)
    return sorted(signals), sorted(silences)


def _question(held: SettledAssignment | NearbyReception) -> ListeningQuery:
    """The listening question for one assignment, on the assignment's own terms."""
    return ListeningQuery(
        station_id=held.station_id,
        satellite_id=held.satellite_id,
        centre_freq_hz=held.centre_freq_hz,
        mode=held.mode,
        window=(held.start_at, held.end_at),
    )


def _informativeness(outcome: str, assignment_id: str) -> tuple[int, str]:
    """Rank a report by :data:`OUTCOME_ORDER`, then by id, as the labeller does."""
    rank = (
        OUTCOME_ORDER.index(outcome) if outcome in OUTCOME_ORDER else len(OUTCOME_ORDER)
    )
    return rank, assignment_id


def _report(report: LatestReport) -> dict[str, object]:
    return {
        "assignment_id": report.assignment_id,
        "observation_id": report.observation_id,
        "revision": report.revision,
        "outcome": report.outcome,
    }
