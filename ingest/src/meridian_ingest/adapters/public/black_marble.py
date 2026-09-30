"""Night-time lights: NASA Black Marble VNP46A3 monthly composites, from LAADS.

**What a value is.** The mean of VIIRS day/night band radiance over a month,
corrected for the atmosphere and moonlight, from near-nadir, snow-free views —
in nanowatts per square centimetre per steradian. Here each value is the mean
over a 0.1° block of the product's 15 arc-second pixels, because a 10° tile is
5.76 million pixels and no area of interest is watched at that grain.

**Resampling is stated, not hidden.** A block's value is the mean of its valid
pixels, and a block with fewer than half its pixels valid is stored as missing
with the count in its reason; ``quality`` records how many pixels the mean is
over. The method is part of ``product``, so a series cites it (D-221).

**Access.** Registration: an Earthdata Login bearer token, presented in an
``Authorization`` header the adapter plans with a placeholder (D-223).

**Two-step planning.** Each granule's name carries its production time, so the
adapter plans the month's directory listing and names granules from the stored
listing (:mod:`meridian_ingest.adapters.listing`). The production time is also
``published_at``, where it precedes our fetch (D-222).

**Format.** HDF-EOS5 read through ``h5py``, the ``hdf5`` extra (D-226). The grid
is ``HDFEOS/GRIDS/VIIRS_Grid_DNB_2d/Data Fields/NearNadir_Composite_Snow_Free``,
unsigned 16-bit with ``_FillValue`` and ``scale_factor`` attributes, covering the
10° tile named ``hHHvVV`` in the granule's name from its north-west corner.

Reference: docs/DECISIONS.md D-220, D-221, D-222, D-223, D-226.
"""

from __future__ import annotations

import io
import json
import re
from datetime import UTC, datetime

from meridian_ingest.adapters.listing import LISTING_SUFFIX
from meridian_ingest.adapters.protocol import (
    FetchRequest,
    SourceDescriptor,
    refuse_a_tile,
)
from meridian_ingest.adapters.public.black_marble_grid import block_samples, months
from meridian_ingest.adapters.public.common import (
    require_bbox,
    require_interval,
    retrieved_at,
    version_from_headers,
)
from meridian_ingest.extent import BoundingBox
from meridian_ingest.normalise.records import NormalisationError, NormalisedBatch
from meridian_ingest.normalise.samples import published_bound
from meridian_ingest.raw_store import StoredArtefact
from meridian_ingest.retrieval import KEY_PLACEHOLDER, RemoteArtefact, RetrievedArtefact

__all__ = [
    "BLACK_MARBLE",
    "GRANULE",
    "BlackMarbleAdapter",
    "BlackMarbleNormaliser",
    "tile_box",
]

BLACK_MARBLE = SourceDescriptor(
    source_id="nasa_black_marble",
    source_class="regional_product",
    name="NASA Black Marble VNP46A3 monthly night-time lights (LAADS)",
    licence="NASA open data; no restrictions on use, citation requested",
    terms_url="https://www.earthdata.nasa.gov/engage/open-data-services-software-policies/data-use-guidance",
    attribution_entry="NASA Black Marble night-time lights",
    access_constraint="registration",
)

ARCHIVE = "https://ladsweb.modaps.eosdis.nasa.gov/archive/allData/5200/VNP46A3"
GRANULE = re.compile(
    r"^VNP46A3\.A(?P<year>\d{4})(?P<doy>\d{3})\.h(?P<h>\d{2})v(?P<v>\d{2})"
    r"\.002\.(?P<produced>\d{13})\.h5$"
)
AUTHORISATION = {"Authorization": f"Bearer {KEY_PLACEHOLDER}"}
TRANSFORMATION_VERSION = "black-marble-1"
TILE_DEG = 10.0


def tile_box(h: int, v: int) -> BoundingBox:
    """The ground one ``hHHvVV`` tile covers."""
    west, north = -180.0 + TILE_DEG * h, 90.0 - TILE_DEG * v
    return BoundingBox(west, north - TILE_DEG, west + TILE_DEG, north)


class BlackMarbleAdapter:
    """Each month's listing, then the granules over the configured box."""

    @property
    def descriptor(self) -> SourceDescriptor:
        """What this source is, and what it may be used under."""
        return BLACK_MARBLE

    def plan(self, request: FetchRequest) -> tuple[RemoteArtefact, ...]:
        """The listing of each month the interval touches.

        Raises:
            ValueError: No box, or no interval.
        """
        require_bbox(request, BLACK_MARBLE.source_id)
        since, until = require_interval(request, BLACK_MARBLE.source_id)
        planned = []
        for start, end in months(since, until)[: request.limit]:
            folder = f"{start.year}/{start.timetuple().tm_yday:03d}"
            planned.append(
                RemoteArtefact(
                    url=f"{ARCHIVE}/{folder}.json",
                    original_identifier=f"VNP46A3/{folder}{LISTING_SUFFIX}",
                    payload_kind="data",
                    headers=AUTHORISATION,
                    valid_from=start,
                    valid_to=end,
                )
            )
        return tuple(planned)

    def expand(
        self, listing: StoredArtefact, request: FetchRequest
    ) -> tuple[RemoteArtefact, ...]:
        """The granules in a stored listing whose tile meets the box."""
        bbox = require_bbox(request, BLACK_MARBLE.source_id)
        folder = listing.manifest.provenance.original_identifier.removeprefix(
            "VNP46A3/"
        ).removesuffix(LISTING_SUFFIX)
        wanted = []
        for name in _names(listing):
            found = GRANULE.fullmatch(name)
            if found is None or not _meets(
                tile_box(int(found["h"]), int(found["v"])), bbox
            ):
                continue
            wanted.append(
                RemoteArtefact(
                    url=f"{ARCHIVE}/{folder}/{name}",
                    original_identifier=name,
                    payload_kind="data",
                    headers=AUTHORISATION,
                    valid_from=listing.manifest.provenance.valid_from,
                    valid_to=listing.manifest.provenance.valid_to,
                )
            )
        return tuple(wanted[: request.limit])

    def source_version(self, retrieved: RetrievedArtefact) -> str:
        """The archive's validator, or when it served the file."""
        return version_from_headers(retrieved)


class BlackMarbleNormaliser:
    """A granule as 0.1° block means; a listing as nothing at all."""

    transformation_version = TRANSFORMATION_VERSION

    def normalise(self, artefact: StoredArtefact) -> NormalisedBatch:
        """Every block of the granule's grid, sparse blocks kept as missing.

        Raises:
            NormalisationError: The granule's name or contents are not the
                product's, or ``h5py`` is not installed.
        """
        provenance = artefact.manifest.provenance
        refuse_a_tile(provenance)
        empty = NormalisedBatch(transformation_version=self.transformation_version)
        if provenance.original_identifier.endswith(LISTING_SUFFIX):
            return empty
        found = GRANULE.fullmatch(provenance.original_identifier)
        if found is None:
            message = f"{provenance.original_identifier} is not a VNP46A3 granule"
            raise NormalisationError(message)
        produced = datetime.strptime(found["produced"], "%Y%j%H%M%S").replace(
            tzinfo=UTC
        )
        published_at, basis = published_bound(produced, retrieved_at(artefact))
        month = datetime.strptime(found["year"] + found["doy"], "%Y%j").replace(
            tzinfo=UTC
        )
        samples = block_samples(
            io.BytesIO(artefact.read_bytes()),
            tile=(int(found["h"]), int(found["v"])),
            month=month,
            published=(published_at, basis),
        )
        return NormalisedBatch(
            transformation_version=self.transformation_version, samples=samples
        )


def _names(listing: StoredArtefact) -> list[str]:
    """File names in a LAADS listing, as a list or under ``content``."""
    try:
        body = json.loads(listing.read_bytes().decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        message = f"{listing.raw_path} is not a readable listing: {exc}"
        raise NormalisationError(message) from exc
    entries = body.get("content", []) if isinstance(body, dict) else body
    if not isinstance(entries, list):
        return []
    return [
        str(entry["name"])
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("name"), str)
    ]


def _meets(tile: BoundingBox, bbox: BoundingBox) -> bool:
    return not (
        tile.east <= bbox.west
        or tile.west >= bbox.east
        or tile.north <= bbox.south
        or tile.south >= bbox.north
    )
