"""Reading the epoch an element set was issued for, and refusing one that is not.

A two-line element set states its epoch in columns 19–32 of line 1, as a
two-digit year and a fractional day. Converting that to an instant is handed to
Skyfield rather than done by slicing the string, for the reason the propagator
boundary exists at all: the format has a pivot year and a fractional day that is
not a duration, and a hand-rolled reader would be right for years and then wrong
once.

**Skyfield does not validate what it is given.** Two lines of arbitrary text are
accepted and yield an epoch of 1999-12-31, which is why this module checks the
lines first. An unnoticed junk element set is the worst kind of failure here: it
loads without complaint, propagates to a position, produces no passes over any
station, and gives nobody a reason why.

Element-set age is a first-class feature everywhere in this project, so the epoch
is not a convenience — it is the quantity the age is measured from. A set loaded
with the wrong epoch makes every prediction from it look fresher or staler than
it is, and nothing downstream can tell.

Confined to ``meridian.orbit`` because it reads a TLE with the propagator
library. Callers elsewhere pass the two lines and get back an instant.

Reference: Vallado, *Fundamentals of Astrodynamics and Applications*, appendix B
on the two-line element set format, including the modulo-10 line checksum.
"""

from __future__ import annotations

from datetime import datetime

from skyfield.api import EarthSatellite, load

__all__ = ["epoch_of"]

# Built once at import. The timescale is immutable and constructing it parses a
# leap-second table, which is work a per-call build would repeat for nothing.
# `builtin=True` is Skyfield's current default, written out because a station
# has to work with the network down and a library default could change without
# us noticing — the same reasoning `SkyfieldOrbitService.__init__` applies.
_TIMESCALE = load.timescale(builtin=True)

ELEMENT_SET_LINE_LENGTH = 69
"""Columns in one line of a two-line element set, including its checksum digit.

Fixed-width by definition: every field is read by column position, so a line of
any other length is not a short element set, it is not an element set.
"""

_CHECKSUM_COLUMN = ELEMENT_SET_LINE_LENGTH - 1
"""Zero-based index of the checksum digit — the last column of the line."""


def epoch_of(line1: str, line2: str) -> datetime:
    """The instant a two-line element set was issued for.

    Args:
        line1: Line 1 of the set, which is the line carrying the epoch.
        line2: Line 2. Required because the pair is validated and parsed
            together, not because the epoch is in it.

    Returns:
        The epoch, as timezone-aware UTC.

    Raises:
        ValueError: The lines are not a well-formed element set — wrong length,
            wrong line number, or a failed checksum. A caller loading a
            catalogue answers all three the same way, so they are one error.

    Note:
        The two-digit year pivots at 57: ``57`` is 1957, the year of Sputnik 1,
        and ``56`` is 2056. That is the format's own convention rather than one
        this project chose, and Skyfield is what applies it.
    """
    _require_element_set_line(line1, 1)
    _require_element_set_line(line2, 2)
    # Skyfield is untyped here, so the instant is narrowed rather than returned
    # straight through — mypy runs with `disallow_any_explicit` and an `Any`
    # escaping this function would switch off checking at every call site.
    epoch: datetime = EarthSatellite(line1, line2, "", _TIMESCALE).epoch.utc_datetime()
    return epoch


def _require_element_set_line(line: str, number: int) -> None:
    """Check one line's width, its line number and its checksum.

    Raises:
        ValueError: Naming which of the three failed, because a catalogue file
            is edited by hand and "invalid element set" sends someone hunting.
    """
    if len(line) != ELEMENT_SET_LINE_LENGTH:
        raise ValueError(
            f"line {number} is {len(line)} columns,"
            f" not {ELEMENT_SET_LINE_LENGTH}: {line!r}"
        )
    if not line.startswith(f"{number} "):
        raise ValueError(f"line {number} does not begin with {number!r}: {line!r}")

    stated = line[_CHECKSUM_COLUMN]
    computed = _checksum(line[:_CHECKSUM_COLUMN])
    if stated != str(computed):
        raise ValueError(
            f"line {number} checksum is {stated!r}, computed {computed}: {line!r}"
        )


def _checksum(columns: str) -> int:
    """The modulo-10 checksum of one element-set line, excluding its own digit.

    Digits count as themselves and a minus sign counts as one; every other
    character counts as nothing. That rule is the format's, and it is why a
    negative exponent in the drag term changes the digit while a decimal point
    does not.
    """
    total = 0
    for column in columns:
        if column.isdigit():
            total += int(column)
        elif column == "-":
            total += 1
    return total % 10
