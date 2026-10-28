"""What a report says, as data: the stored results a template is filled with.

A report is rendered from facts, never from rows directly. The facts are what
the store held for one pass or one week, read from the rows a report names in
its ``inputs_json`` (D-302), so the same rows always make the same facts and
the same facts always make the same text.

``inputs_sha256`` is the hash of the facts as canonical JSON. A regeneration
reads the named rows again and must reproduce it before the text is compared,
so a changed row is told apart from a changed template.

Nothing here reads the database or knows a recipient. Standard library only.

Reference: docs/DECISIONS.md D-098, D-302.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime

__all__ = [
    "CauseTested",
    "ChainWarningFact",
    "DiagnosisFact",
    "Facts",
    "PassFacts",
    "VerdictFact",
    "WeeklyFacts",
    "facts_json",
    "inputs_sha256",
]


@dataclass(frozen=True, slots=True)
class VerdictFact:
    """The verdict a reception was read against."""

    probability_usable: float
    partial_below: float
    method: str


@dataclass(frozen=True, slots=True)
class CauseTested:
    """One cause the diagnosis tested, and whether its test fired."""

    cause: str
    fired: bool


@dataclass(frozen=True, slots=True)
class DiagnosisFact:
    """Why a reception was lost, and every cause tested to say so."""

    cause: str
    """One of Stage 27's five causes, or ``undetermined``."""

    tested: tuple[CauseTested, ...]
    method: str


@dataclass(frozen=True, slots=True)
class PassFacts:
    """One assignment's pass: what was received, and what was concluded."""

    station_id: str
    station_name: str
    simulated: bool
    satellite_id: str
    satellite_name: str
    window_start: datetime
    window_end: datetime
    max_elevation_deg: float
    outcome: str | None
    """The current report's outcome, or ``None`` when the station sent none."""

    peak_snr_db: float | None
    frames_decoded: int | None
    verdict: VerdictFact | None
    """``None`` when no verdict was written for the reception."""

    diagnosis: DiagnosisFact | None
    """``None`` for a reception that was not lost."""


@dataclass(frozen=True, slots=True)
class ChainWarningFact:
    """A receive-chain warning open at the end of the week."""

    band: str
    freq_min_hz: int
    freq_max_hz: int
    raised_at: datetime
    shortfall_db: float


@dataclass(frozen=True, slots=True)
class WeeklyFacts:
    """One station's week, Monday 00:00 UTC to the next."""

    station_id: str
    station_name: str
    simulated: bool
    period_start: datetime
    period_end: datetime
    hours_heard: int
    """Hours of the week in which at least one heartbeat arrived."""

    hours: int
    passes: int
    """Passes the station held to the end of their window."""

    outcomes: tuple[tuple[str, int], ...]
    """``(outcome, count)``, ``no_report`` for a pass with none, in the
    template's order."""

    verdicts: tuple[tuple[str, int], ...]
    """``(bucket, count)`` over decoded receptions, ``unscored`` included."""

    causes: tuple[tuple[str, int], ...]
    """``(cause, count)`` over diagnosed losses, most frequent first."""

    warnings: tuple[ChainWarningFact, ...]


Facts = PassFacts | WeeklyFacts


def facts_json(facts: Facts) -> dict[str, object]:
    """The facts as plain JSON values: instants in UTC, tuples as lists."""
    kind = "pass" if isinstance(facts, PassFacts) else "weekly"
    return {"kind": kind, "facts": _plain(dataclasses.asdict(facts))}


def inputs_sha256(facts: Facts) -> bytes:
    """The sha256 of the facts as canonical JSON: keys sorted, no spaces."""
    text = json.dumps(
        facts_json(facts), sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(text.encode("utf-8")).digest()


def _plain(value: object) -> object:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError(f"an instant without a zone cannot be a fact: {value}")
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    return value
