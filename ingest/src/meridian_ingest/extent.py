"""Where on the ground a fetch asks about: points and a bounding box.

Most public products are asked for by place — a forecast at a coordinate, fire
detections inside a box. The place comes from the settings file, never from an
adapter's literal, so one adapter serves every deployment.

**A point is rounded before it leaves the machine.** A point is often a
station, and a station's exact position is something D-082 lets its owner
publish less precisely than we store it. Sending it at full precision to a
third party would publish it anyway, in their logs. So every point an adapter
sends is rounded to :data:`SENT_DECIMALS` places — about a kilometre — unless
the adapter rounds harder; the products asked about are gridded at that scale
or coarser, so nothing measurable is lost (D-220).

Reference: docs/DECISIONS.md D-082, D-220.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["SENT_DECIMALS", "BoundingBox", "ExtentError", "GeoPoint"]

SENT_DECIMALS = 2
"""Decimal places of a degree a point keeps when sent to a source."""

_MAX_LAT = 90.0
_MAX_LON = 180.0


class ExtentError(ValueError):
    """A place that is not on the globe, or a box with no inside."""


@dataclass(frozen=True, slots=True)
class GeoPoint:
    """A latitude and a longitude, in degrees, ISO 6709 ranges.

    Raises:
        ExtentError: Either is out of range.
    """

    lat_deg: float
    lon_deg: float

    def __post_init__(self) -> None:
        """Refuse a point off the globe."""
        if not -_MAX_LAT <= self.lat_deg <= _MAX_LAT:
            message = f"latitude {self.lat_deg} is outside -90 to 90"
            raise ExtentError(message)
        if not -_MAX_LON <= self.lon_deg <= _MAX_LON:
            message = f"longitude {self.lon_deg} is outside -180 to 180"
            raise ExtentError(message)

    def sent(self, decimals: int = SENT_DECIMALS) -> GeoPoint:
        """This point as it may be sent to a third party."""
        return GeoPoint(round(self.lat_deg, decimals), round(self.lon_deg, decimals))


@dataclass(frozen=True, slots=True)
class BoundingBox:
    """West, south, east, north, in degrees. Never crosses the antimeridian.

    Raises:
        ExtentError: A side is off the globe, or the box has no inside.

    Note:
        A box crossing 180° would need two requests to most sources and two
        rows of arithmetic everywhere else; nothing Meridian watches is there,
        so it is refused rather than half supported.
    """

    west: float
    south: float
    east: float
    north: float

    def __post_init__(self) -> None:
        """Refuse a box that is not a box."""
        GeoPoint(self.south, self.west)
        GeoPoint(self.north, self.east)
        if not (self.west < self.east and self.south < self.north):
            message = (
                f"box {self.west},{self.south},{self.east},{self.north} has no "
                "inside: west must be less than east and south less than north"
            )
            raise ExtentError(message)

    def contains(self, lat_deg: float, lon_deg: float) -> bool:
        """Whether a point lies inside, edges included."""
        return self.south <= lat_deg <= self.north and self.west <= lon_deg <= self.east

    def as_text(self) -> str:
        """``west,south,east,north``, the order most services take."""
        return f"{self.west:g},{self.south:g},{self.east:g},{self.north:g}"
