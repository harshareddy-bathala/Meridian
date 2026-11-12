"""The network's weekly report: its facts, and template ``network-weekly-1``.

The public counterpart of an owner's ``weekly-1``, about the whole network
rather than one station, and read by anyone on the dashboard's reports page.
It says what the week's settled passes came to, the capture rate and the loss
budget SC-4 is judged by, why passes were lost, and how many receive-chain
warnings were open at the end.

**Measured and simulated apart, always** (rule 5). Each is its own section, and
the simulated one opens by saying so. A deployment with no simulated station
prints no simulated section, and one with no measured station says that in
words (D-086).

**Counts only.** No station, satellite, pass or place is named, so nothing here
can be read back into a schedule or a position (D-093, D-187).

**The same rules as the template beside it** (D-303): a function of its facts
alone, every word from :mod:`~meridian.notifications.wording`, a released
version never edited, its hash pinned by a test on fixed facts, plain text
only, and no language model anywhere (D-098).

Standard library only.

Reference: docs/DECISIONS.md D-086, D-098, D-182, D-303, D-306, D-367.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from meridian.notifications.facts import plain_json
from meridian.notifications.templates import Rendered
from meridian.notifications.wording import CLASS_NAMES, cause_name

__all__ = [
    "CURRENT_NETWORK",
    "NETWORK_TEMPLATES",
    "SIMULATED_HEADING",
    "NetworkWeeklyFacts",
    "PopulationWeek",
    "network_inputs_sha256",
    "render_network",
]

SIMULATED_HEADING = "SIMULATED STATIONS — not real receptions."


@dataclass(frozen=True, slots=True)
class PopulationWeek:
    """Measured or simulated stations' week, never pooled."""

    simulated: bool
    stations: int
    """Stations registered by the end of the week and not removed."""

    stations_heard: int
    """Of every station, those whose heartbeat arrived during the week."""

    passes: int
    """Classified passes whose window closed in the week."""

    classes: tuple[tuple[str, int], ...]
    """``(class, count)`` for each class with a pass, in the classification's
    order."""

    captured: int
    eligible: int
    """Passes a station could have captured: SC-4's denominator."""

    capture_low: float | None
    capture_high: float | None
    """The capture rate's Wilson 95% interval, ``None`` when nothing is
    eligible."""

    lost: int
    """Eligible passes not captured, which spend the loss budget."""

    allowed: float
    """Passes the week could lose with SC-4's target still met."""

    causes: tuple[tuple[str, int], ...]
    """``(cause, count)`` over the week's diagnosed losses, most frequent first."""

    warnings_open: int
    """Receive-chain warnings open at the end of the week."""


@dataclass(frozen=True, slots=True)
class NetworkWeeklyFacts:
    """The network's week, Monday 00:00 UTC to the next."""

    period_start: datetime
    period_end: datetime
    classification_method: str
    classification_sha256: str
    """The configuration the passes were classified under, as hex."""

    capture_target: float
    measured: PopulationWeek
    simulated: PopulationWeek


def network_inputs_sha256(facts: NetworkWeeklyFacts) -> bytes:
    """The sha256 of the facts as canonical JSON: keys sorted, no spaces."""
    text = json.dumps(
        plain_json(dataclasses.asdict(facts)),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(text.encode("utf-8")).digest()


def _network_weekly_1(facts: NetworkWeeklyFacts) -> Rendered:
    week = f"{facts.period_start.astimezone(UTC):%Y-%m-%d}"
    subject = f"Meridian network, week of {week}"
    lines = [
        f"Week: {week} to {facts.period_end.astimezone(UTC):%Y-%m-%d}, Monday to"
        " Monday UTC. A pass belongs to the week its window closed in.",
        "",
        "MEASURED STATIONS",
        *_population(facts.measured, facts.capture_target),
    ]
    if facts.simulated.stations:
        lines += [
            "",
            SIMULATED_HEADING,
            *_population(facts.simulated, facts.capture_target),
        ]
    lines += [
        "",
        f"Counted under {facts.classification_method}, configuration"
        f" {facts.classification_sha256[:12]}. SC-4's target is a capture rate of"
        f" {_percent(facts.capture_target)}.",
        "Written from Meridian's stored results by template network-weekly-1; no"
        " part of it was generated.",
    ]
    return Rendered(subject=subject, body="\n".join(lines))


def _population(week: PopulationWeek, target: float) -> list[str]:
    kind = "simulated" if week.simulated else "measured"
    if not week.stations:
        return [f"No {kind} station is registered."]
    lines = [
        f"Stations: {week.stations} registered, {week.stations_heard} heard from"
        " during the week."
    ]
    if not week.passes:
        return [*lines, "Passes: none closed in the week and was settled."]
    named = ", ".join(f"{CLASS_NAMES[key]} {count}" for key, count in week.classes)
    return [
        *lines,
        f"Passes settled: {week.passes}; {named}.",
        _capture(week),
        *_budget(week, target),
        _warnings(week.warnings_open),
    ]


def _capture(week: PopulationWeek) -> str:
    if not week.eligible or week.capture_low is None or week.capture_high is None:
        return (
            "Capture rate: nothing to count, because every pass was judged by its"
            " satellite rather than the station."
        )
    return (
        f"Capture rate: {week.captured} of {week.eligible} passes a station could"
        f" have captured, {_percent(week.captured / week.eligible)}, with a 95%"
        f" interval of {_percent(week.capture_low)} to"
        f" {_percent(week.capture_high)}."
    )


def _budget(week: PopulationWeek, target: float) -> list[str]:
    if not week.eligible:
        return []
    state = "over the budget" if week.lost > week.allowed else "within the budget"
    lines = [
        f"Loss budget: {week.lost} lost, against {week.allowed:g} the week could"
        f" lose at a {_percent(target)} target: {state}."
    ]
    if week.causes:
        counted = ", ".join(f"{cause_name(key)} {n}" for key, n in week.causes)
        lines.append(f"Losses by cause: {counted}.")
    elif week.lost:
        lines.append("Losses by cause: none diagnosed yet.")
    return lines


def _warnings(count: int) -> str:
    if not count:
        return "Receive chains: no warning open at the end of the week."
    noun = "warning" if count == 1 else "warnings"
    return f"Receive chains: {count} {noun} open at the end of the week."


NETWORK_TEMPLATES: dict[str, Callable[[NetworkWeeklyFacts], Rendered]] = {
    "network-weekly-1": _network_weekly_1
}
"""Every released version. One is added; none is edited or removed."""

CURRENT_NETWORK = "network-weekly-1"
"""The version a network report is rendered by."""


def render_network(version: str, facts: NetworkWeeklyFacts) -> Rendered:
    """Render ``facts`` by template ``version``.

    Raises:
        KeyError: No network template has this version.
    """
    return NETWORK_TEMPLATES[version](facts)


def _percent(share: float) -> str:
    return f"{round(100 * share)}%"
