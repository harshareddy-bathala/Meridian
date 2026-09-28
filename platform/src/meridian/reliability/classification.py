"""What happened to a scheduled pass — the one definition of a miss.

Every reliability figure the project publishes, and every label a dataset
carries, comes from :func:`classify`. The snapshot labeller calls it on rows
read from a raw snapshot (D-143); the live accounting calls it on rows read
from the database. Each gathers its own evidence and neither restates the
rules, so a pass cannot be a miss in a report and something else in a dataset.

The rules are a first-match table, read top to bottom:

1. the report is ``decoded`` → ``successful_reception``;
2. the report is ``signal_no_decode`` → ``signal_no_decode``;
3. the report is ``aborted`` or ``not_attempted`` → ``station_unavailable``;
4. no heartbeat at all arrived in the window → ``station_unavailable``;
5. no report, and every scheduled assignment expired → ``assignment_declined``;
6. listening was not confirmed → ``station_not_confirmed_listening``;
7. listening was confirmed and nothing was heard → ``confirmed_miss``,
   ``satellite_silent`` or ``satellite_state_indeterminate``, by D-147.

A heartbeat is looked for before a decline is read (D-181). An assignment
expires whenever its window closes untaken, which is also what happens to one
handed to a station that was not there to take it; only a station that was
heard while the window was open can be said to have declined.

**Absence is not a miss.** Rule 7 is the only road to ``confirmed_miss``, and
it needs the registry's own answer that the station was listening on the right
frequency for the right target (``Registry.was_listening``, CLAUDE.md rule 7).

This module imports the standard library and nothing else, so the labelling
path, which may reach no database, can call it (D-180).

Reference: docs/DECISIONS.md D-146, D-147, D-180, D-181.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from meridian.reliability.satellite_silence import SatelliteState

__all__ = [
    "CAPTURED",
    "MISS",
    "PASS_CLASSES",
    "SATELLITE_CLASSES",
    "PassClass",
    "PassEvidence",
    "classify",
]

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
"""What counts as captured for SC-4. A decode below Stage 27's partial
threshold may later count too; until that rule exists only a decode does."""

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
    """The state of every scheduled assignment of the pass; never empty."""

    heard_during_window: bool
    """Whether any heartbeat from the station arrived inside any of those
    assignments' windows."""

    listening_confirmed: bool | None
    """``Registry.was_listening``'s answer for any of them; None if unasked."""

    def __post_init__(self) -> None:
        """Refuse a pass nothing scheduled; the labeller excludes those first."""
        if not self.assignment_states:
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
    rules: tuple[tuple[Callable[[], bool], PassClass], ...] = (
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
