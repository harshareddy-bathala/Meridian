"""What a ``[sources.<id>]`` table asks a source about.

Places — ``points``, ``bbox`` and ``layers`` — and, for a catalogue of element
sets, objects — ``groups`` and ``catalog_numbers``. Split from
:mod:`meridian_ingest.config` because they are the only settings with shape,
and each refusal has to say which shape it was. What is asked about is
configuration, never an adapter literal, so one adapter serves every
deployment (D-220, D-320).

Reference: docs/DECISIONS.md D-220, D-320.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from meridian_ingest.extent import BoundingBox, ExtentError, GeoPoint

__all__ = ["PLACE_KEYS", "PlaceError", "SourcePlaces", "parse_places"]

PLACE_KEYS = frozenset({"points", "bbox", "layers", "groups", "catalog_numbers"})

_PAIR = 2
_BOX = 4


class PlaceError(ValueError):
    """A place key with the wrong shape, or a place off the globe."""


@dataclass(frozen=True, slots=True)
class SourcePlaces:
    """Where one source is asked about, as the settings file says."""

    points: tuple[GeoPoint, ...] = ()
    bbox: BoundingBox | None = None
    layers: tuple[str, ...] = ()
    groups: tuple[str, ...] = ()
    """Named groups of a catalogue, each fetched whole in one request."""

    catalog_numbers: tuple[int, ...] = ()
    """Catalogue numbers fetched one request each, for an object no group holds."""


def parse_places(entry: Mapping[str, object], where: str) -> SourcePlaces:
    """The place keys of one source table.

    Args:
        entry: The ``[sources.<id>]`` table.
        where: Its name, for messages.

    Returns:
        What it says, each key defaulting to nothing.

    Raises:
        PlaceError: A key has the wrong shape or names a place off the globe.
    """
    return SourcePlaces(
        points=_points(entry.get("points"), where),
        bbox=_bbox(entry.get("bbox"), where),
        layers=_names(entry.get("layers"), f"{where}.layers", "layer names"),
        groups=_names(entry.get("groups"), f"{where}.groups", "group names"),
        catalog_numbers=_catalog_numbers(entry.get("catalog_numbers"), where),
    )


def _points(value: object, where: str) -> tuple[GeoPoint, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        message = f"{where}.points must be a list of [latitude, longitude] pairs"
        raise PlaceError(message)
    points = []
    for index, pair in enumerate(value):
        numbers = _numbers(pair, _PAIR, f"{where}.points[{index}]")
        try:
            points.append(GeoPoint(numbers[0], numbers[1]))
        except ExtentError as exc:
            message = f"{where}.points[{index}]: {exc}"
            raise PlaceError(message) from exc
    return tuple(points)


def _bbox(value: object, where: str) -> BoundingBox | None:
    if value is None:
        return None
    west, south, east, north = _numbers(value, _BOX, f"{where}.bbox")
    try:
        return BoundingBox(west, south, east, north)
    except ExtentError as exc:
        message = f"{where}.bbox: {exc}"
        raise PlaceError(message) from exc


def _names(value: object, where: str, what: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(
        isinstance(one, str) and one.strip() for one in value
    ):
        message = f"{where} must be a list of {what}"
        raise PlaceError(message)
    return tuple(str(one).strip() for one in value)


def _catalog_numbers(value: object, where: str) -> tuple[int, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(
        isinstance(one, int) and not isinstance(one, bool) and one > 0 for one in value
    ):
        message = f"{where}.catalog_numbers must be a list of positive whole numbers"
        raise PlaceError(message)
    return tuple(int(one) for one in value)


def _numbers(value: object, count: int, where: str) -> list[float]:
    if (
        not isinstance(value, list)
        or len(value) != count
        or not all(
            isinstance(one, int | float) and not isinstance(one, bool) for one in value
        )
    ):
        message = f"{where} must be {count} numbers"
        raise PlaceError(message)
    return [float(one) for one in value]
