"""Choosing which passes a station spends its time on.

More passes are visible than a station can receive — one antenna, one receiver,
and passes that overlap. This package decides which ones win, and records why
each loser lost. docs/PROJECT.md §13 calls the screen that shows that reasoning
"the entire project", so the reasoning is produced *during* scheduling and
stored with the decision rather than reconstructed from the outcome afterwards.

Two deliberately naive baselines live here from Stage 7: configuration **A**,
which ranks candidates by maximum elevation alone (``elevation_baseline``), and
configuration **B**, which adds operator priority. They exist to be beaten. The
constrained optimiser that beats them is Stage 18, arriving after prediction
because it consumes prediction outputs; docs/EVALUATION.md measures SC-1 as
**D − B**, not D − A, so the elevation-only baseline is a floor to report
against rather than the comparison that counts.

This module declares value types only. The ranking functions and the non-overlap
rule are separate leaf modules that take these types and return them, perform no
I/O and read no clock — so a schedule can be checked against candidates written
out by hand, with no database and no propagator.

Candidates arrive from ``meridian.store.passes`` by way of the scheduler run,
never from the observation store: what a station *did* is not an input to what it
should be asked to do next (docs/ARCHITECTURE.md).

Reference: docs/EVALUATION.md §3; docs/DECISIONS.md D-065.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

__all__ = [
    "Candidate",
    "Rejection",
    "ScheduleOutcome",
    "ScoredCandidate",
]


@dataclass(frozen=True, slots=True)
class Candidate:
    """One predicted pass a station could be asked to receive.

    A narrow view of a ``passes`` row: what a scheduling decision turns on, and
    nothing else. The frequency and mode an assignment needs on the wire
    (MSP §4.3) are absent because no ranking or conflict rule consults them —
    the run that writes the assignment reads them from the pass and the
    transmitter, and a candidate carrying them would invite a ranking function
    to start using them without saying so.
    """

    pass_id: int
    """Identifies the *prediction*, not the physical pass.

    One pass may be predicted more than once, from different element sets
    (D-063). Within a single scheduler run each prediction is its own candidate,
    so this is a unique handle on the decision being made.
    """

    station_id: str
    aos: datetime
    los: datetime
    """Both timezone-aware UTC, and both the *pass* boundaries.

    An assignment's window is wider — the pass opened out by the platform's
    stated timing uncertainty (D-021, D-060) — and that widening happens when
    the assignment is written. Conflicts are judged on the pass, so two
    schedules computed under different uncertainty models stay comparable.
    """

    max_elevation_deg: float
    """Culmination. Configuration A ranks on this alone."""

    priority: float
    """The operator's weight for this satellite. Configuration B adds it."""

    simulated: bool
    """Copied from the pass, which copied it from the station (D-013).

    Carried through scheduling so the assignment written at the end inherits it
    rather than defaulting. A simulated pass produces a simulated assignment at
    every layer (CLAUDE.md rule 5).
    """


@dataclass(frozen=True, slots=True)
class ScoredCandidate:
    """A candidate and what the configured ranking function returned for it.

    The score travels with the candidate rather than being recomputed at each
    step, so the number stored on the assignment is provably the number the
    decision was made with.
    """

    candidate: Candidate
    score: float
    """Unitless here. The unit is a property of the configuration that produced
    it — degrees for A, a priority-weighted quantity for B — which is why
    ``assignments.score`` is read alongside ``model_config`` or not at all."""


@dataclass(frozen=True, slots=True)
class Rejection:
    """A candidate that lost, and the candidate it lost to.

    ``conflicts_with_pass_id`` names a *pass* because at this point no
    assignment exists yet — assignment ids are minted when the schedule is
    written. The run that writes it maps this to the winning assignment's id for
    ``assignments.conflicts_with_assignment_id``, which is what a reader needs
    later: across runs a pass id identifies an opportunity, not a decision.
    """

    scored: ScoredCandidate
    conflicts_with_pass_id: int


@dataclass(frozen=True, slots=True)
class ScheduleOutcome:
    """One station-time allocation: what was taken, and what was displaced.

    Both halves, always. A scheduler that returned only its selections would
    make the skipped passes unrecoverable — they are not in the output and not
    in any table — and the dashboard would have nothing to explain.
    """

    selected: list[ScoredCandidate]
    """In the order they were taken, which is ranking order rather than time
    order. The schedule as a timeline is a presentation concern."""

    rejected: list[Rejection]
