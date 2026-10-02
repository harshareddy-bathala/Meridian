"""What happened to a scheduled pass — the one definition of a miss.

Every reliability figure the project publishes, and every label a dataset
carries, comes from :func:`classify`. The snapshot labeller calls it on rows
read from a raw snapshot (D-143); the live accounting calls it on rows read
from the database. Each gathers its own evidence and neither restates the
rules, so a pass cannot be a miss in a report and something else in a dataset.

The rules are a first-match table, read top to bottom:

1. no report, and every assignment was taken back before its window, one of
   them because the station declined it → ``assignment_declined``;
2. no report, and every assignment was taken back → ``station_unavailable``:
   the station was offline when a round ran, and the work was withdrawn;
3. the report is ``decoded`` → ``successful_reception``;
4. the report is ``signal_no_decode`` → ``signal_no_decode``;
5. the report is ``aborted`` or ``not_attempted`` → ``station_unavailable``;
6. no heartbeat at all arrived in the window → ``station_unavailable``;
7. no report, and every assignment left with the station expired →
   ``assignment_declined``;
8. listening was not confirmed → ``station_not_confirmed_listening``;
9. listening was confirmed and nothing was heard → ``confirmed_miss``,
   ``satellite_silent`` or ``satellite_state_indeterminate``, by D-147.

**A revoked assignment is never the station's work** (D-171). The platform
took it back before its window began, so it is never a miss, and rules 6 to 9
read only the assignments left with the station.

A heartbeat is looked for before a decline is read (D-181). An assignment
expires whenever its window closes untaken, which is also what happens to one
handed to a station that was not there to take it; only a station that was
heard while the window was open can be said to have declined.

**Absence is not a miss.** Rule 9 is the only road to ``confirmed_miss``, and
it needs the registry's own answer that the station was listening on the right
frequency for the right target (``Registry.was_listening``, CLAUDE.md rule 7).

This module imports the standard library and nothing else, so the labelling
path, which may reach no database, can call it (D-180).

Reference: docs/DECISIONS.md D-146, D-147, D-171, D-180, D-181.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from meridian.reliability.satellite_silence import SatelliteState

__all__ = [
    "CAPTURED",
    "METHOD",
    "MISS",
    "OUTCOME_ORDER",
    "PASS_CLASSES",
    "SATELLITE_CLASSES",
    "PassClass",
    "PassEvidence",
    "classify",
]

METHOD = "classification-1"
"""Recorded with every classification the live accounting stores. Bumped
whenever a rule here or in :mod:`~meridian.reliability.satellite_silence`
changes, so a stored row always names the rules that produced it (D-182)."""

OUTCOME_ORDER = ("decoded", "signal_no_decode", "no_signal", "aborted", "not_attempted")
"""Most informative first. Where several assignments of one physical pass
reported, the report whose outcome comes first here is the pass's (D-146)."""

PassClass = Literal[
    "successful_reception",
    "signal_no_decode",
    "confirmed_miss",
    "satellite_silent",
    "satellite_state_indeterminate",
    "station_unavailable",
    "station_not_confirmed_listening",
    "assignment_declined",
]

PASS_CLASSES: tuple[PassClass, ...] = (
    "successful_reception",
    "signal_no_decode",
    "confirmed_miss",
    "satellite_silent",
    "satellite_state_indeterminate",
    "station_unavailable",
    "station_not_confirmed_listening",
    "assignment_declined",
)
"""Every class, in the order manifests and reports list them."""

CAPTURED: frozenset[PassClass] = frozenset({"successful_reception"})
"""What counts as captured for SC-4: a decode, whatever its verdict (D-271).

A decode the reception verdict puts below its partial threshold still counts.
SC-4 asks whether the station received the pass; whether the product is usable
is SC-7's question, read from the verdict. Counting it lost would make SC-4
move with every refit of a model fitted on rated receptions, and differ between
a deployment with a verdict model and one without. The verdict informs: such a
decode is diagnosed as a partial reception (D-102, D-272). It does not decide
SC-4, and nothing in this module reads a verdict."""

MISS: PassClass = "confirmed_miss"
"""The one class that is a miss."""

SATELLITE_CLASSES: frozenset[PassClass] = frozenset(
    {"satellite_silent", "satellite_state_indeterminate"}
)
"""Classes that say nothing about the station, so SC-4 leaves them out."""

_UNAVAILABLE = frozenset(("aborted", "not_attempted"))
_BY_SATELLITE_STATE: dict[SatelliteState, PassClass] = {
    "transmitting": "confirmed_miss",
    "silent": "satellite_silent",
    "indeterminate": "satellite_state_indeterminate",
}


@dataclass(frozen=True, slots=True)
class PassEvidence:
    """What is known about one scheduled pass once its window has settled."""

    outcome: str | None
    """The report's outcome, or None if no report arrived."""

    assignment_states: tuple[str, ...]
    """The state of every scheduled assignment left with the station: every
    one not revoked. Empty only if every assignment was revoked."""

    heard_during_window: bool
    """Whether any heartbeat from the station arrived inside any of those
    assignments' windows."""

    listening_confirmed: bool | None
    """``Registry.was_listening``'s answer for any of them; None if unasked."""

    revoked_reasons: tuple[str, ...] = ()
    """``revoked_reason`` of each assignment taken back before its window:
    ``declined`` or ``offline`` (D-171)."""

    def __post_init__(self) -> None:
        """Refuse a pass nothing scheduled; the labeller excludes those first."""
        if not self.assignment_states and not self.revoked_reasons:
            raise ValueError("a pass nothing scheduled has no classification")


def classify(
    evidence: PassEvidence, satellite_state: Callable[[], SatelliteState]
) -> PassClass:
    """Classify one settled, scheduled pass.

    Args:
        evidence: What the report, the heartbeats and the registry say.
        satellite_state: Judges whether the satellite was transmitting
            (D-147). Called only when the station was confirmed listening and
            heard nothing, since gathering that evidence reads other passes.

    Returns:
        The pass's class.
    """
    outcome = evidence.outcome
    taken_back = outcome is None and not evidence.assignment_states
    rules: tuple[tuple[Callable[[], bool], PassClass], ...] = (
        (
            lambda: taken_back and "declined" in evidence.revoked_reasons,
            "assignment_declined",
        ),
        (lambda: taken_back, "station_unavailable"),
        (lambda: outcome == "decoded", "successful_reception"),
        (lambda: outcome == "signal_no_decode", "signal_no_decode"),
        (lambda: outcome in _UNAVAILABLE, "station_unavailable"),
        (lambda: not evidence.heard_during_window, "station_unavailable"),
        (
            lambda: (
                outcome is None
                and all(one == "expired" for one in evidence.assignment_states)
            ),
            "assignment_declined",
        ),
        (lambda: not evidence.listening_confirmed, "station_not_confirmed_listening"),
    )
    for holds, pass_class in rules:
        if holds():
            return pass_class
    return _BY_SATELLITE_STATE[satellite_state()]
