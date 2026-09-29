"""Registering an area of interest: a place and a label, and nothing about a person.

The first record in Meridian that describes a place someone cares about rather
than a satellite (D-137). Until the team settles who may register one and
whether a registration is public, the interim answer is narrow (D-227):

* **an operator registers an area at the command line**, as stations are
  admitted — there is no endpoint, public or MSP, that creates one;
* **nothing about an area is published** by the API or the dashboard;
* **a label is refused if it looks like it names a person's contact** — an
  email address, a phone number, a street address. An area describes ground,
  and the moment it describes a person it becomes personal data the project
  does not hold (``PROJECT.md`` §16).

Reference: docs/DECISIONS.md D-137, D-227.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from meridian.regions.geometry import GeometryError, Polygon

__all__ = ["AreaRefusedError", "NewArea", "new_area"]

MAX_LABEL = 120

_PHONE_RUN = re.compile(r"\+?\d[\d\s().-]{7,}\d")
_PHONE_DIGITS = 10
"""A run of digits and separators with at least this many digits is read as a
phone number. Ten, so a date such as 2026-09-01 (eight) is not."""

_CONTACT = (
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "an email address"),
    (
        re.compile(
            r"\b\d+\s+\w+\s+(street|st|road|rd|lane|avenue|ave|nagar|cross|main)\b",
            re.IGNORECASE,
        ),
        "a street address",
    ),
)


class AreaRefusedError(ValueError):
    """An area that would describe a person, or is not one shape on the globe."""


@dataclass(frozen=True, slots=True)
class NewArea:
    """An area in insertable form, its derived columns computed from its shape."""

    label: str
    polygon: Polygon
    notes: str | None = None

    @property
    def centroid(self) -> tuple[float, float]:
        """``(lat_deg, lon_deg)``."""
        return self.polygon.centroid()

    @property
    def area_km2(self) -> float:
        """Its area in square kilometres."""
        return self.polygon.area_km2()


def new_area(label: str, polygon: Polygon, notes: str | None = None) -> NewArea:
    """An area ready to store, or a refusal saying why.

    Args:
        label: What the area is called. 1 to 120 characters.
        polygon: Its shape.
        notes: Anything else an operator wants to say about the ground.

    Returns:
        The area.

    Raises:
        AreaRefusedError: The label is empty or too long, the label or notes
            look like a person's contact, or the shape has no area.
    """
    cleaned = label.strip()
    if not 1 <= len(cleaned) <= MAX_LABEL:
        message = f"a label is 1 to {MAX_LABEL} characters"
        raise AreaRefusedError(message)
    for text in (cleaned, notes or ""):
        _refuse_contact(text)
    try:
        area = polygon.area_km2()
    except (GeometryError, ZeroDivisionError) as exc:
        raise AreaRefusedError(str(exc)) from exc
    if area <= 0:
        message = "the shape encloses no ground"
        raise AreaRefusedError(message)
    return NewArea(
        label=cleaned, polygon=polygon, notes=notes.strip() if notes else None
    )


def _refuse_contact(text: str) -> None:
    found = [what for pattern, what in _CONTACT if pattern.search(text)]
    if any(
        sum(char.isdigit() for char in run) >= _PHONE_DIGITS
        for run in _PHONE_RUN.findall(text)
    ):
        found.append("a phone number")
    if found:
        message = (
            f"this looks like {found[0]}. An area of interest is a place and a "
            "label, never a person or their contact (D-137, D-227)"
        )
        raise AreaRefusedError(message)
