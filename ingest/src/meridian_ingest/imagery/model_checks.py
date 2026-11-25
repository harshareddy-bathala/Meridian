"""The rules every imagery record is checked by, and the vocabulary they share.

The base of :mod:`.model`: the constants a record is held to, the error a
broken rule raises, and the small checks each record's ``__post_init__`` and
``from_json`` are written from. Public within the package so the record
modules share one copy of each rule; :mod:`.model` is what everything else
imports.

Stdlib only.

Reference: docs/DECISIONS.md D-371.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Literal

MANIFEST_SCHEMA = "imagery-manifest-1"
INDEX_SCHEMA = "imagery-index-1"
METHOD_VERSION = "imagery-1"
"""The pipeline's version, in every manifest. A change to what a scene renders
to is a new version, so an old directory still names the method that made it."""

MAX_SIDE_PX = 4096
"""The longest side a stored window may have. A larger area is read from the
nearest overview under it, never resampled by us (D-372)."""

SourceClass = Literal["high_resolution", "older_record", "daily_wide"]
SOURCE_CLASSES: tuple[SourceClass, ...] = (
    "high_resolution",
    "older_record",
    "daily_wide",
)
"""The public archive's classes (D-371). Images received by Meridian's own
stations arrive with Stage 40, which adds their class; until then the gallery
says in words that there are none."""

AnnotationKind = Literal["fire", "area", "change", "place"]
ANNOTATION_KINDS: tuple[AnnotationKind, ...] = ("fire", "area", "change", "place")

InputKind = Literal[
    "catalogue", "scene", "firms", "gazetteer", "areas_summary", "template"
]
INPUT_KINDS: tuple[InputKind, ...] = (
    "catalogue",
    "scene",
    "firms",
    "gazetteer",
    "areas_summary",
    "template",
)
"""What a manifest names as an input. Each is a raw artefact, a published file
or a template, named by its digest, so a scene can be rendered again from the
same inputs and land on the same directory (rule 8)."""

REQUIRED_FILES = ("scene.tif", "scene.png", "scene.pgw", "display.png")
"""The four files every scene publishes beside its tiles."""

SOURCE_ID = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
HASH12 = re.compile(r"^[0-9a-f]{12}$")
EPSG = re.compile(r"^EPSG:[0-9]{4,6}$")
FILE = re.compile(
    r"^(?:tiles/[0-9]{1,2}/[0-9]{1,7}/[0-9]{1,7}\.webp"
    r"|[a-z0-9_]{1,40}\.(?:tif|png|pgw|txt))$"
)
"""A published file's path: a tile ``tiles/<z>/<x>/<y>.webp``, or one flat name.
No string from a source ever becomes one (D-141); these are names we choose."""

TEXT_MAX = 2000
LAT_LIMIT = 90.0
LON_LIMIT = 180.0
BOX_SIDES = 4
AFFINE_TERMS = 6
PIXEL_AXES = 2
ZOOM_LIMIT = 22
"""XYZ's deepest level. Nothing open is finer than a tile at 22."""


class MalformedImageryError(ValueError):
    """A record that breaks one of this module's rules, named in the message."""


def fail(message: str) -> MalformedImageryError:
    """The error a broken rule raises, carrying ``message``."""
    return MalformedImageryError(message)


def text(value: object, field: str, *, maximum: int = TEXT_MAX) -> str:
    """``value`` as non-empty text no longer than ``maximum``."""
    if not isinstance(value, str) or not value.strip():
        raise fail(f"{field} must be non-empty text")
    if len(value) > maximum:
        raise fail(f"{field} is longer than {maximum} characters")
    return value


def number(value: object, field: str) -> float:
    """``value`` as a finite number, never a boolean."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise fail(f"{field} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise fail(f"{field} must be finite")
    return number


def integer(value: object, field: str, *, minimum: int = 0) -> int:
    """``value`` as an integer of at least ``minimum``, never a boolean."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise fail(f"{field} must be an integer")
    if value < minimum:
        raise fail(f"{field} must be at least {minimum}")
    return value


def https(value: object, field: str) -> str:
    """``value`` as text that is an https URL."""
    written = text(value, field)
    if not written.startswith("https://"):
        raise fail(f"{field} must be an https URL")
    return written


SAME_ORIGIN = re.compile(r"^/api/v1/[a-z0-9/_-]{1,100}$")
"""A path on the platform's own public API, the one relative address a
citation may give."""


def citation_url(value: object, field: str) -> str:
    """``value`` as an https URL, or a path on the platform's own public API."""
    written = text(value, field)
    if not written.startswith("https://") and not SAME_ORIGIN.match(written):
        raise fail(f"{field} must be an https URL or an /api/v1/ path")
    return written


def instant(value: datetime, field: str) -> str:
    """An aware instant written as ``2026-11-14T05:30:00Z``."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise fail(f"{field} must carry a time zone")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_instant(value: object, field: str) -> datetime:
    """An instant read back from ``2026-11-14T05:30:00Z``."""
    written = text(value, field, maximum=20)
    try:
        parsed = datetime.strptime(written + "+0000", "%Y-%m-%dT%H:%M:%SZ%z")
    except ValueError:
        message = f"{field} must be a UTC instant like 2026-11-14T05:30:00Z"
        raise fail(message) from None
    return parsed.astimezone(UTC)


def fields(stored: object, expected: frozenset[str], what: str) -> Mapping[str, object]:
    """``stored`` as an object with exactly the ``expected`` keys."""
    if not isinstance(stored, Mapping):
        raise fail(f"{what} must be a JSON object")
    unknown = sorted(set(stored) - expected)
    if unknown:
        raise fail(f"{what} carries fields this version does not know: {unknown}")
    missing = sorted(expected - set(stored))
    if missing:
        raise fail(f"{what} is missing {missing}")
    return stored


def sequence(value: object, field: str) -> Sequence[object]:
    """``value`` as a JSON array."""
    if not isinstance(value, list):
        raise fail(f"{field} must be a JSON array")
    return value


def not_a_class(value: object) -> str:
    """The refusal for a source class this version does not know."""
    return f"source_class {value!r} is not one of {SOURCE_CLASSES}"


def source_id(value: object) -> None:
    """Refuse ``value`` unless it is one of our source ids."""
    if not isinstance(value, str) or not SOURCE_ID.match(value):
        raise fail(f"source_id {value!r} is not a source id")
