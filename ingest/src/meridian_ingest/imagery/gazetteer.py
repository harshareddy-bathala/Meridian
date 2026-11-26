"""Which places label a scene: the gazetteer by the area's size, and names thinned.

**The rule.** An area whose box is at most :data:`SMALL_AREA_KM` on its longer
side is labelled from GeoNames' ``cities1000``, which names its towns and
neighbourhoods. A larger one is labelled from Natural Earth's populated
places, which names only what a picture that wide can carry. An area may
override the rule in ``ingest.toml`` (D-374).

**Thinning is deterministic.** Places inside the box are taken by population,
largest first, then by name and id, and a place is dropped when it lies
closer than a tenth of the box's longer side to one already kept. At most
:data:`MAX_PLACES` are kept. The same gazetteer file and the same box always
give the same labels, so a scene's manifest does not change unless a record
does.

**Read from the raw store, never fetched here.** The newest held file of the
chosen gazetteer is read; ``follow`` keeps it fresh. When none is held, the
scene has no place labels and says so.

Record-only: no image library, no NumPy (D-371). A label is placed by the
gazetteer's coordinates, never found in the picture.

Reference: docs/DECISIONS.md D-133, D-326, D-371, D-374.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from meridian_ingest.adapters.public.gazetteers import (
    GEONAMES,
    NATURAL_EARTH,
    Place,
    read_geonames,
    read_natural_earth,
)
from meridian_ingest.imagery.graticule import metres_per_degree
from meridian_ingest.imagery.model import Annotation, Bounds, Citation, Input, Point
from meridian_ingest.raw_store import RawStore

__all__ = [
    "MAX_PLACES",
    "SMALL_AREA_KM",
    "PlaceLabels",
    "gazetteer_for",
    "long_side_km",
    "place_labels",
    "thin",
]

SMALL_AREA_KM = 50.0
"""The longest box GeoNames labels. A city or a district fits under it, and
its towns are what a reader of a 10 m scene looks for; a box larger than this
would be crowded with villages."""

MAX_PLACES = 12
SPACING_SHARE = 0.1
"""How close two labels may be, as a share of the box's longer side."""

_READERS = {
    GEONAMES.source_id: read_geonames,
    NATURAL_EARTH.source_id: read_natural_earth,
}
_NAMED_BY = {GEONAMES.source_id: "GeoNames", NATURAL_EARTH.source_id: "Natural Earth"}


def long_side_km(bounds: Bounds) -> float:
    """The box's longer side on the ground, at its centre latitude."""
    east, north = metres_per_degree((bounds.south + bounds.north) / 2.0)
    width = (bounds.east - bounds.west) * east
    height = (bounds.north - bounds.south) * north
    return max(width, height) / 1000.0


def gazetteer_for(bounds: Bounds, override: str | None = None) -> str:
    """The gazetteer an area is labelled from: its override, else the rule."""
    if override is not None:
        return override
    if long_side_km(bounds) <= SMALL_AREA_KM:
        return GEONAMES.source_id
    return NATURAL_EARTH.source_id


@dataclass(frozen=True, slots=True)
class PlaceLabels:
    """The places that label one scene, and the file they were read from.

    ``raw_path`` and ``sha256`` are None when no file of the gazetteer is held,
    and ``places`` is then empty: a scene says it has no labels rather than
    labelling itself from somewhere else.
    """

    source_id: str
    raw_path: str | None
    sha256: str | None
    places: tuple[Place, ...]

    @property
    def named_by(self) -> str:
        """The gazetteer's name, as a credit and a caption give it."""
        return _NAMED_BY[self.source_id]

    def inputs(self) -> tuple[Input, ...]:
        """The file, as a manifest names it, when one was read."""
        if self.raw_path is None or self.sha256 is None:
            return ()
        return (Input("gazetteer", self.raw_path, self.sha256),)

    def annotations(self) -> tuple[Annotation, ...]:
        """One marker per place, each citing its gazetteer row."""
        return tuple(self._annotation(one) for one in self.places)

    def _annotation(self, place: Place) -> Annotation:
        if self.source_id == GEONAMES.source_id:
            citation = Citation(
                GEONAMES.source_id,
                f"geonameid {place.record_id}",
                f"https://www.geonames.org/{place.record_id}",
                GEONAMES.licence,
            )
        else:
            citation = Citation(
                NATURAL_EARTH.source_id,
                f"ne_id {place.record_id}",
                "https://www.naturalearthdata.com/",
                NATURAL_EARTH.licence,
            )
        people = (
            f" of {place.population:,} people as {self.named_by} lists it"
            if place.population
            else f" in {self.named_by}"
        )
        return Annotation(
            kind="place",
            geometry=Point(place.lat_deg, place.lon_deg),
            label=place.name,
            text=f"{place.name}, a populated place{people}",
            citations=(citation,),
        )


def place_labels(
    store: RawStore, bounds: Bounds, override: str | None = None
) -> PlaceLabels:
    """The places that label a scene over ``bounds``, from the newest held file."""
    source_id = gazetteer_for(bounds, override)
    held = store.scan(source_id)
    if not held:
        return PlaceLabels(source_id, None, None, ())
    raw_path = held[-1]
    artefact = store.read(raw_path)
    places = _read(source_id, artefact.artefact)
    return PlaceLabels(
        source_id=source_id,
        raw_path=raw_path,
        sha256=artefact.manifest.sha256.hex(),
        places=thin(places, bounds),
    )


@lru_cache(maxsize=2)
def _read(source_id: str, path: Path) -> tuple[Place, ...]:
    """A gazetteer file read once per run. A raw artefact never changes (D-141)."""
    return _READERS[source_id](path.read_bytes())


def thin(
    places: Sequence[Place], bounds: Bounds, limit: int = MAX_PLACES
) -> tuple[Place, ...]:
    """The places inside ``bounds`` a picture can carry, chosen the same way each time.

    Largest first, then by name and id; a place closer than
    :data:`SPACING_SHARE` of the box's longer side to one already kept is
    dropped. The kept places are returned in that order.
    """
    inside = [one for one in places if bounds.contains(Point(one.lat_deg, one.lon_deg))]
    inside.sort(key=lambda one: (-one.population, one.name, one.record_id))
    spacing_km = SPACING_SHARE * long_side_km(bounds)
    east, north = metres_per_degree((bounds.south + bounds.north) / 2.0)
    kept: list[Place] = []
    for place in inside:
        if len(kept) == limit:
            break
        if all(_km(place, other, east, north) >= spacing_km for other in kept):
            kept.append(place)
    return tuple(kept)


def _km(one: Place, other: Place, east: float, north: float) -> float:
    """The distance between two places in a box, on a flat local grid."""
    dx = (one.lon_deg - other.lon_deg) * east
    dy = (one.lat_deg - other.lat_deg) * north
    return math.hypot(dx, dy) / 1000.0
