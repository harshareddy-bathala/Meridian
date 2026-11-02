"""A lost pass and its diagnosis, as the public API states it (Stage 35, D-331).

Built from ``store.loss_reads.ListedLoss``. The window is widened to whole
minutes (D-093), as every published window is.

**Every cause tested is published, fired or not** (D-273), each with its support
and what it found. *Undetermined* is published as the answer it is.

**What a test found is published from a list, not verbatim.** A diagnosis's
``candidates_json`` is free-form, written for an operator, and holds exact
angles, a station's raw noise floor and the sectors its own history marks as
blocked. :data:`PUBLISHED_FINDINGS` names, per cause, each finding the public
API states and how it is rounded: an angle to the degree (D-093), decibels and
seconds to a tenth, a share and a support to a hundredth. A finding not listed is
not published, and ``meridian diagnosis explain`` prints the whole record.
``docs/THREAT-MODEL.md`` §7 reviews the list.

This module builds no SQL and opens no connection.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel

from meridian.api.public.window_privacy import publish_window
from meridian.store.loss_reads import ListedLoss

__all__ = ["PUBLISHED_FINDINGS", "PublicCauseTest", "PublicLoss", "published_findings"]

Finding = int | float | str | bool | None

_RAW = "raw"
_DEGREE = "degree"
_TENTH = "tenth"
_HUNDREDTH = "hundredth"

_DIGITS: dict[str, int | None] = {_RAW: None, _DEGREE: 0, _TENTH: 1, _HUNDREDTH: 2}
"""Decimal places each rule keeps; ``None`` keeps the value as stored."""

PUBLISHED_FINDINGS: dict[str, dict[str, str]] = {
    "station_not_listening": {
        "classification": _RAW,
        "listening_confirmed": _RAW,
        "heard_during_window": _RAW,
        "outcome": _RAW,
    },
    "satellite_silent": {
        "catalogue_active": _RAW,
        "signals": _RAW,
        "silences": _RAW,
        "peak_elevation_deg": _DEGREE,
        "state": _RAW,
        "reason": _RAW,
    },
    "obstruction": {
        "samples": _RAW,
        "history": _RAW,
        "declared_bins": _RAW,
        "lost": _RAW,
        "lost_inside": _RAW,
        "audible": _RAW,
        "audible_inside": _RAW,
        "share": _HUNDREDTH,
        "reason": _RAW,
    },
    "interference": {
        "baseline_count": _RAW,
        "lift_db": _TENTH,
    },
    "timing_fault": {
        "allowed_s": _TENTH,
        "listening_s": _TENTH,
        "clock_skew_s": _TENTH,
        "reported_offset_s": _TENTH,
        "recording_s": _TENTH,
    },
}
"""Per cause, each finding published and its rounding; anything else is withheld.

Withheld on purpose: the obstruction test's ``marked`` sectors, which describe
what stands around a station; the interference test's raw floor, baseline and
profile cell, which are the station's own levels at a gain; and every id that
names a row the public API does not publish.
"""


def published_findings(cause: str, found: dict[str, object]) -> dict[str, Finding]:
    """What a cause's test found, as the public API may state it.

    Args:
        cause: The cause tested, a key of :data:`PUBLISHED_FINDINGS`.
        found: The test's record, as ``loss_diagnoses.candidates_json`` holds it.

    Returns:
        Only the listed findings the record holds, each rounded by its rule. A
        cause not listed publishes none.
    """
    rules = PUBLISHED_FINDINGS.get(cause, {})
    return {key: _rounded(found[key], rules[key]) for key in rules if key in found}


def _rounded(value: object, rule: str) -> Finding:
    """One finding, rounded by ``rule``; a non-number passes through as text."""
    if value is None or isinstance(value, bool | str):
        return value
    if not isinstance(value, int | float):
        return str(value)
    digits = _DIGITS[rule]
    if digits is None:
        return value
    return round(value) if digits == 0 else round(value, digits)


class PublicCauseTest(BaseModel):
    """One cause's test: whether it fired, how strongly, and what it found."""

    cause: str
    fired: bool
    support: float
    """0 when it did not fire; from 0.5 up when it did (D-273); to a hundredth."""
    findings: dict[str, Finding]
    """The listed findings only (:data:`PUBLISHED_FINDINGS`)."""


Cause = Literal[
    "satellite_silent",
    "station_not_listening",
    "obstruction",
    "interference",
    "timing_fault",
    "undetermined",
]


class PublicLoss(BaseModel):
    """One lost pass, its diagnosed cause, and every cause that was tested."""

    diagnosis_id: int
    assignment_id: str
    station_id: str
    satellite_id: str
    window_start: datetime
    """Floored to the minute (D-093)."""
    window_end: datetime
    """Ceiled to the minute (D-093)."""
    classification: str
    """The pass's class (Stage 20), which the diagnosis read."""
    cause: Cause
    tested: list[PublicCauseTest]
    method: str
    computed_at: datetime
    simulated: bool

    @classmethod
    def from_row(cls, row: ListedLoss) -> Self:
        """Publish one diagnosed loss."""
        window = publish_window(row.window_start, row.window_end)
        return cls(
            diagnosis_id=row.diagnosis_id,
            assignment_id=row.assignment_id,
            station_id=row.station_id,
            satellite_id=row.satellite_id,
            window_start=window.start,
            window_end=window.end,
            classification=row.classification,
            cause=_cause(row.cause),
            tested=[_test(one) for one in row.candidates_json],
            method=row.method,
            computed_at=row.computed_at,
            simulated=row.simulated,
        )


def _test(held: dict[str, object]) -> PublicCauseTest:
    """Publish one stored candidate."""
    cause = str(held["cause"])
    found = held.get("found")
    support = held.get("support")
    return PublicCauseTest(
        cause=cause,
        fired=bool(held["fired"]),
        support=round(float(support), 2) if isinstance(support, int | float) else 0.0,
        findings=published_findings(cause, found if isinstance(found, dict) else {}),
    )


def _cause(value: str) -> Cause:
    """Narrow the column's text to the vocabulary its CHECK constraint allows."""
    for cause in (
        "satellite_silent",
        "station_not_listening",
        "obstruction",
        "interference",
        "timing_fault",
        "undetermined",
    ):
        if value == cause:
            return cause
    raise ValueError(f"loss_diagnoses.cause holds {value!r}, outside its CHECK")
