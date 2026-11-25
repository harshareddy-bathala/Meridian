"""A scene's caption: a versioned plain-text function of its facts, and its alt text.

``imagery-caption-1`` says what the picture is, where and when, which
satellite and instrument took it, how much cloud lies over the area and how
that was found, the pixel size the files keep, and each kind of marker with
its source. It is the picture's alt text too, so a reader who cannot see the
picture is told what a reader who can is shown.

**The D-303 pattern.** The caption is a function of :class:`CaptionFacts`
alone: no clock, no locale, no environment. Every sentence it can say is in
this file. A released version is never edited: a test pins its digest on fixed
facts, and a changed sentence is a new version beside the old one, so a
published manifest's caption still regenerates to the digest it states.

**Absence is said as absence.** A FIRMS day nobody fetched is "not checked",
never "no fire"; a gazetteer never fetched is "no place names", never "no
places". A cloud share the source does not publish is not stated.

Record-only: stdlib, no image library (D-371).

Reference: docs/DECISIONS.md D-133, D-303, D-374.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from meridian_ingest.imagery.model import Bounds, SourceClass

__all__ = ["CAPTION_TEMPLATE", "CaptionFacts", "caption"]

CAPTION_TEMPLATE = "imagery-caption-1"

_WHAT: dict[SourceClass, str] = {
    "high_resolution": "High-resolution satellite image",
    "older_record": "Satellite image from the longer Landsat record",
    "daily_wide": "Daily wide-view satellite image",
}


@dataclass(frozen=True, slots=True)
class CaptionFacts:
    """Everything a caption says, and nothing it would have to look up.

    ``places_named_by`` is the gazetteer's name when one of its files was
    held, else None; ``gazetteer`` names it either way.
    """

    area_label: str
    source_class: SourceClass
    platform: str
    instrument: str
    acquired_at: datetime
    bounds: Bounds
    resolution_m: float
    cloud_fraction: float | None
    cloud_method: str
    credit: str
    changes: int
    fire_days: tuple[date, ...]
    fire_days_held: tuple[date, ...]
    fires_detected: int
    fires_shown: int
    gazetteer: str
    places_named_by: str | None
    places: int


def caption(facts: CaptionFacts) -> str:
    """The caption of ``imagery-caption-1``, in plain text."""
    sentences = (
        _what(facts),
        _where(facts),
        _cloud(facts),
        "Marked: " + "; ".join(_markers(facts)) + ".",
        f"Public archive imagery, credited: {facts.credit}",
    )
    return " ".join(_ended(one) for one in sentences)


def _what(facts: CaptionFacts) -> str:
    at = facts.acquired_at
    return (
        f"{_WHAT[facts.source_class]} of {facts.area_label}, taken by "
        f"{facts.platform}'s {facts.instrument} on {_day(at.date())} at "
        f"{at:%H:%M} UTC"
    )


def _where(facts: CaptionFacts) -> str:
    box = facts.bounds
    return (
        f"It covers {_lat(box.south)} to {_lat(box.north)} and {_lon(box.west)} "
        f"to {_lon(box.east)}, and its files keep the source's "
        f"{facts.resolution_m:,.0f} m pixels"
    )


def _cloud(facts: CaptionFacts) -> str:
    if facts.cloud_fraction is None:
        return f"No cloud share is stated: {facts.cloud_method}"
    percent = facts.cloud_fraction * 100.0
    if percent == 0:
        return f"No cloud lies over the area's box, {facts.cloud_method}"
    share = "less than 1%" if percent < 1 else f"{percent:.0f}%"
    return f"Cloud covers {share} of the area's box, {facts.cloud_method}"


def _markers(facts: CaptionFacts) -> list[str]:
    changes = (
        "" if facts.changes == 0 else f" and {_count(facts.changes, 'change')} over it"
    )
    return [
        f"the area's bounding box{changes}, from Meridian's published areas summary",
        _fires(facts),
        _places(facts),
    ]


def _fires(facts: CaptionFacts) -> str:
    missing = [one for one in facts.fire_days if one not in facts.fire_days_held]
    if not facts.fire_days_held:
        return (
            f"fires not checked, as no NASA FIRMS record was held for {_days(missing)}"
        )
    if facts.fires_detected == 0:
        found = "no active fire detected by NASA FIRMS within a day of the image"
    elif facts.fires_shown < facts.fires_detected:
        found = (
            f"the {facts.fires_shown} most powerful of "
            f"{_count(facts.fires_detected, 'active fire detection')} by NASA "
            "FIRMS within a day of the image"
        )
    else:
        found = (
            f"{_count(facts.fires_detected, 'active fire detection')} by NASA "
            "FIRMS within a day of the image"
        )
    if missing:
        found += f", with {_days(missing)} not checked, as no record was held"
    return found


def _places(facts: CaptionFacts) -> str:
    if facts.places_named_by is None:
        return f"no place names, as no {facts.gazetteer} file was held"
    if facts.places == 0:
        return f"no {facts.places_named_by} place lies inside the box"
    return f"{_count(facts.places, 'place name')} from {facts.places_named_by}"


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _day(day: date) -> str:
    return f"{day.day} {day:%B %Y}"


def _days(days: Sequence[date]) -> str:
    written = [one.isoformat() for one in days]
    if len(written) == 1:
        return written[0]
    return ", ".join(written[:-1]) + " and " + written[-1]


def _lat(value: float) -> str:
    return f"{abs(value):.2f}°{'N' if value >= 0 else 'S'}"


def _lon(value: float) -> str:
    return f"{abs(value):.2f}°{'E' if value >= 0 else 'W'}"


def _ended(sentence: str) -> str:
    return sentence if sentence.endswith(".") else sentence + "."
