"""Versioned templates: facts in, plain text out, and nothing else.

A template is a function of its facts alone. It reads no clock, no locale, no
database and no environment, so the same facts and version give the same
bytes on any machine, and ``content_sha256`` proves a regeneration matches what
was sent (D-302).

**A released version is never edited.** A changed sentence, order or format is
a new version beside the old, and a report keeps the version it was rendered
by. ``tests/unit/test_notification_templates.py`` pins each version's hash on a
fixed set of facts, so an edit to a released one fails there (D-303).

**Plain text only.** Email is sent as ``text/plain`` and Telegram without a
parse mode, so there is no markup to escape and none to inject; a station's
own name is cleaned before it is printed (:func:`~meridian.notifications.
wording.clean`).

**A simulated station's report says so first**, in its subject and its first
line (rule 5).

No language model, anywhere (D-098). Standard library only.

Reference: docs/DECISIONS.md D-098, D-302, D-303.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from meridian.notifications.facts import (
    DiagnosisFact,
    Facts,
    PassFacts,
    VerdictFact,
    WeeklyFacts,
)
from meridian.notifications.wording import (
    CAUSES,
    OUTCOME_NAMES,
    OUTCOMES,
    UNSCORED,
    cause_name,
    clean,
    verdict_bucket,
)

__all__ = [
    "CURRENT",
    "SIMULATED_LINE",
    "TEMPLATES",
    "Rendered",
    "content_sha256",
    "render",
]

SIMULATED_LINE = "SIMULATED STATION — not a real reception."


@dataclass(frozen=True, slots=True)
class Rendered:
    """A report as sent: a subject and a plain-text body."""

    subject: str
    body: str

    def encoded(self) -> bytes:
        """The bytes ``content_sha256`` is taken over."""
        return f"{self.subject}\n\n{self.body}".encode()


def content_sha256(rendered: Rendered) -> bytes:
    """The sha256 of a report's subject and body."""
    return hashlib.sha256(rendered.encoded()).digest()


def _pass_1(facts: PassFacts) -> Rendered:
    satellite = clean(facts.satellite_name)
    station = clean(facts.station_name)
    if facts.diagnosis is not None:
        headline = f"lost, {cause_name(facts.diagnosis.cause)}"
    else:
        headline = OUTCOME_NAMES[facts.outcome or "no_report"]
    subject = f"Meridian: {satellite} over {station}, {_minute(facts.window_start)}"
    subject = f"{subject} UTC: {headline}"

    lines = _opening(facts.simulated, station, facts.station_id)
    lines += [
        f"Pass: {satellite} ({facts.satellite_id}), {_minute(facts.window_start)}"
        f" to {facts.window_end.astimezone(UTC):%H:%M} UTC, highest elevation"
        f" {facts.max_elevation_deg:.0f}°.",
        "",
        f"What was received: {OUTCOMES[facts.outcome or 'no_report']}",
    ]
    if facts.peak_snr_db is not None:
        lines.append(f"Strongest signal: {facts.peak_snr_db:.1f} dB above the noise.")
    if facts.frames_decoded is not None:
        lines.append(f"Frames decoded: {facts.frames_decoded}.")
    if facts.outcome == "decoded":
        lines += ["", _verdict_line(facts.verdict)]
    if facts.diagnosis is not None:
        lines += ["", *_diagnosis_lines(facts.diagnosis)]
    lines += _closing("pass-1")
    return Rendered(subject=_marked(facts.simulated, subject), body="\n".join(lines))


def _weekly_1(facts: WeeklyFacts) -> Rendered:
    station = clean(facts.station_name)
    week = f"{facts.period_start.astimezone(UTC):%Y-%m-%d}"
    subject = _marked(facts.simulated, f"Meridian: {station}, week of {week}")

    lines = _opening(facts.simulated, station, facts.station_id)
    lines += [
        f"Week: {week} to {facts.period_end.astimezone(UTC):%Y-%m-%d}, Monday to"
        " Monday UTC.",
        "",
        f"Heard from: heartbeats in {facts.hours_heard} of {facts.hours} hours"
        f" ({_percent(facts.hours_heard, facts.hours)}).",
    ]
    if facts.passes == 0:
        lines.append("Passes: none were held to the end of their window.")
    else:
        lines.append(f"Passes held to the end of their window: {facts.passes}.")
        lines.append(f"  What was received: {_counts(facts.outcomes, OUTCOME_NAMES)}.")
    if any(count for _, count in facts.verdicts):
        lines.append(f"  Verdicts on decoded passes: {_counts(facts.verdicts)}.")
    if facts.causes:
        named = {cause: cause_name(cause) for cause, _ in facts.causes}
        lines.append(f"  Losses by cause: {_counts(facts.causes, named)}.")
    elif facts.passes:
        lines.append("  Losses by cause: none diagnosed.")
    lines.append("")
    if not facts.warnings:
        lines.append("Receive chains: no warning open at the end of the week.")
    for warning in facts.warnings:
        lines.append(
            f"Receive chain warning: the {clean(warning.band)} chain"
            f" ({warning.freq_min_hz / 1e6:.3f}-{warning.freq_max_hz / 1e6:.3f}"
            f" MHz) has been under a warning since {_minute(warning.raised_at)}"
            f" UTC, raised when it was {warning.shortfall_db:.1f} dB short of its"
            " own past signal."
        )
    lines += _closing("weekly-1")
    return Rendered(subject=subject, body="\n".join(lines))


PASS_TEMPLATES: dict[str, Callable[[PassFacts], Rendered]] = {"pass-1": _pass_1}
WEEKLY_TEMPLATES: dict[str, Callable[[WeeklyFacts], Rendered]] = {"weekly-1": _weekly_1}
TEMPLATES = frozenset(PASS_TEMPLATES) | frozenset(WEEKLY_TEMPLATES)
"""Every released version. One is added; none is edited or removed."""

CURRENT = {"pass": "pass-1", "weekly": "weekly-1"}
"""The version a new report is rendered by."""


def render(version: str, facts: Facts) -> Rendered:
    """Render ``facts`` by template ``version``.

    Raises:
        KeyError: No template has this version.
        TypeError: The facts are not the kind this version renders.
    """
    if version not in TEMPLATES:
        raise KeyError(version)
    if isinstance(facts, PassFacts) and version in PASS_TEMPLATES:
        return PASS_TEMPLATES[version](facts)
    if isinstance(facts, WeeklyFacts) and version in WEEKLY_TEMPLATES:
        return WEEKLY_TEMPLATES[version](facts)
    kind = "pass" if isinstance(facts, PassFacts) else "weekly"
    raise TypeError(f"template {version} does not render a {kind} report")


def _opening(simulated: bool, station: str, station_id: str) -> list[str]:
    first = [SIMULATED_LINE, ""] if simulated else []
    return [*first, f"Station: {station} ({station_id})"]


def _closing(version: str) -> list[str]:
    return [
        "",
        f"Written from Meridian's stored results by template {version}; no part"
        " of it was generated. The station's page holds the full record.",
    ]


def _marked(simulated: bool, subject: str) -> str:
    return f"[SIMULATED] {subject}" if simulated else subject


def _verdict_line(verdict: VerdictFact | None) -> str:
    if verdict is None:
        return f"Verdict: {UNSCORED}."
    line = (
        f"Verdict: {verdict.probability_usable:.2f} probability the reception is"
        f" usable, {verdict_bucket(verdict.probability_usable)}."
    )
    if verdict.probability_usable < verdict.partial_below:
        line += (
            f" That is below the partial threshold of {verdict.partial_below:.2f},"
            " so it counts as a partial reception."
        )
    return line


def _diagnosis_lines(diagnosis: DiagnosisFact) -> list[str]:
    lines = [f"Cause of the loss: {CAUSES[diagnosis.cause]}"]
    others = [one for one in diagnosis.tested if one.cause != diagnosis.cause]
    weaker = [cause_name(one.cause) for one in others if one.fired]
    unshown = [cause_name(one.cause) for one in others if not one.fired]
    if weaker:
        lines.append(f"Also shown, less strongly: {', '.join(weaker)}.")
    if unshown:
        lines.append(
            f"Also tested, and not shown by the evidence: {', '.join(unshown)}."
        )
    return lines


def _counts(
    counts: tuple[tuple[str, int], ...], names: dict[str, str] | None = None
) -> str:
    words = names or {}
    return ", ".join(f"{words.get(key, key)} {count}" for key, count in counts)


def _percent(part: int, whole: int) -> str:
    return "0%" if whole == 0 else f"{round(100 * part / whole)}%"


def _minute(instant: datetime) -> str:
    return f"{instant.astimezone(UTC):%Y-%m-%d %H:%M}"
