"""Turning a scheduling outcome into the rows that record it.

A :class:`~meridian.scheduler.ScheduleOutcome` says which passes were taken and
which were displaced. This module turns that into ``assignments`` rows: it mints
each decision's id, widens the pass window into the window a station is actually
asked to record, and writes the sentence a dashboard shows for it.

Pure. It performs no I/O and reads no clock — the facts about each pass arrive
already gathered, so the rows a run would write can be checked without a
database.

**Skips become rows too.** The scheduler considered those passes and decided
against them, and docs/PROJECT.md §13 calls the screen explaining those
decisions "the entire project". A skipped pass that left no row would be
unrecoverable the moment the run ended.

Reference: docs/DECISIONS.md D-021 (the assignment window is not the pass
window), D-060 (the timing prior), D-065, D-066.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta

from meridian.scheduler import ScheduleOutcome, ScoredCandidate
from meridian.store.assignments import NewAssignment

__all__ = [
    "PassFacts",
    "assignment_id_for",
    "to_assignment_rows",
    "widened_window",
]

ASSIGNMENT_ID_PREFIX = "as_"
"""Matches the ``ob_`` convention ``observations.observation_id`` uses."""

ASSIGNMENT_ID_HEX_LENGTH = 12
"""Twelve hex characters — 48 bits, the same width as an observation id.

Enough that a collision across every decision this project will ever make is
not worth reasoning about, and short enough to read out loud in a viva.
"""


@dataclass(frozen=True, slots=True)
class PassFacts:
    """What a decision needs about one pass beyond the candidate itself.

    Gathered by the run — the transmitter comes from the catalogue and the
    timing uncertainty from the propagator — and passed in, so the row building
    here stays free of both.
    """

    centre_freq_hz: int
    mode: str
    """The downlink the station will be asked to receive (MSP §4.3)."""

    aos: datetime
    los: datetime
    timing_uncertainty_s: float
    """The platform's stated 1σ confidence in those two boundaries (D-060)."""


def assignment_id_for(pass_id: int, model_config: str) -> str:
    """The id for the decision this configuration makes about this pass.

    Args:
        pass_id: The prediction being decided about.
        model_config: Which configuration is deciding — ``A``, ``B``, and later
            ``C`` and ``D``.

    Returns:
        ``as_`` followed by twelve hex characters.

    Note:
        **Derived, not random**, following ``observations.observation_id``
        (D-027). Re-running the scheduler over one horizon therefore mints the
        same ids, which is what lets the insert collapse onto
        ``assignment_decision_unique`` instead of writing a second schedule
        (D-066) — and what lets a skip name its winner without a round trip to
        find out what id that winner was given.

        ``model_config`` is in the digest because running two configurations
        over one horizon is exactly what the ablation in docs/EVALUATION.md §3
        requires. Keyed on the pass alone, A and B would collide and the second
        run would silently write nothing.
    """
    digest = hashlib.sha256(f"{pass_id}:{model_config}".encode()).hexdigest()
    return f"{ASSIGNMENT_ID_PREFIX}{digest[:ASSIGNMENT_ID_HEX_LENGTH]}"


def widened_window(facts: PassFacts) -> tuple[datetime, datetime]:
    """The recording window for a pass, opened out by the timing uncertainty.

    Args:
        facts: The pass boundaries and the platform's 1σ confidence in them.

    Returns:
        Start and end, both timezone-aware UTC.

    Note:
        **An assignment's window is not the pass** (D-021). A station told to
        record from exactly the predicted acquisition starts after a pass whose
        element set was stale has already begun, and loses the opening of it —
        which is the part carrying the rise, the part that most often decides
        whether the decode locks.

        Widened by the *stated* uncertainty rather than a fixed pad, so a fresh
        element set costs almost nothing and a week-old one opens the window by
        seconds. A fixed pad would over-record on fresh data and under-record on
        stale, and would stop being self-describing the moment the prior is
        replaced by a fitted model (D-060).
    """
    margin = timedelta(seconds=facts.timing_uncertainty_s)
    return facts.aos - margin, facts.los + margin


def _selection_row(
    scored: ScoredCandidate, facts: PassFacts, model_config: str
) -> NewAssignment:
    """One taken pass, as a row."""
    start_at, end_at = widened_window(facts)
    candidate = scored.candidate
    return NewAssignment(
        assignment_id=assignment_id_for(candidate.pass_id, model_config),
        pass_id=candidate.pass_id,
        station_id=candidate.station_id,
        start_at=start_at,
        end_at=end_at,
        centre_freq_hz=facts.centre_freq_hz,
        mode=facts.mode,
        timing_uncertainty_s=facts.timing_uncertainty_s,
        decision="scheduled",
        reason=(
            f"selected under configuration {model_config}; "
            f"score {scored.score:.1f}, peaking at "
            f"{candidate.max_elevation_deg:.1f} degrees"
        ),
        model_config=model_config,
        score=scored.score,
        conflicts_with_assignment_id=None,
        priority=candidate.priority,
        simulated=candidate.simulated,
    )


def _rejection_row(
    scored: ScoredCandidate, facts: PassFacts, model_config: str, winner_id: str
) -> NewAssignment:
    """One displaced pass, as a row naming what displaced it."""
    start_at, end_at = widened_window(facts)
    candidate = scored.candidate
    return NewAssignment(
        assignment_id=assignment_id_for(candidate.pass_id, model_config),
        pass_id=candidate.pass_id,
        station_id=candidate.station_id,
        start_at=start_at,
        end_at=end_at,
        centre_freq_hz=facts.centre_freq_hz,
        mode=facts.mode,
        timing_uncertainty_s=facts.timing_uncertainty_s,
        decision="skipped",
        reason=(
            f"skipped under configuration {model_config}; score "
            f"{scored.score:.1f}, and the station is already committed to "
            f"{winner_id} over this window"
        ),
        model_config=model_config,
        score=scored.score,
        conflicts_with_assignment_id=winner_id,
        priority=candidate.priority,
        simulated=candidate.simulated,
    )


def to_assignment_rows(
    outcome: ScheduleOutcome,
    facts_by_pass_id: dict[int, PassFacts],
    model_config: str,
) -> list[NewAssignment]:
    """Every decision in one outcome, as rows ready to insert.

    Args:
        outcome: What the ranking and the non-overlap rule concluded.
        facts_by_pass_id: The transmitter and timing facts for every candidate
            in ``outcome``, keyed by ``pass_id``.
        model_config: The configuration that produced the outcome.

    Returns:
        Selections first, then rejections.

    Raises:
        KeyError: A candidate has no entry in ``facts_by_pass_id``. Raised
            rather than skipped: a decision the scheduler made and then dropped
            for want of a frequency would leave the schedule looking complete
            and quietly missing a pass.

    Note:
        **Selections come first, and the order is load bearing.** Each rejection
        names the selection that displaced it through a foreign key, so a batch
        that wrote rejections first would fail on a row referencing an
        assignment that does not exist yet.
    """
    rows = [
        _selection_row(scored, facts_by_pass_id[scored.candidate.pass_id], model_config)
        for scored in outcome.selected
    ]

    rows.extend(
        _rejection_row(
            rejection.scored,
            facts_by_pass_id[rejection.scored.candidate.pass_id],
            model_config,
            assignment_id_for(rejection.conflicts_with_pass_id, model_config),
        )
        for rejection in outcome.rejected
    )

    return rows
