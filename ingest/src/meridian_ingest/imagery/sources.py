"""The three open archives a published area is imaged from, and what each offers.

Each source is one of the public archive's classes (D-371): Sentinel‑2 for the
sharpest recent view, Landsat for the older and longer record, and GIBS for a
daily wide one. A source is described by its terms first, through the same
:class:`~meridian_ingest.adapters.protocol.SourceDescriptor` every fetched
source carries, so the attribution check runs before its first request (D-134).

**Not in the fetch registry.** ``meridian-ingest fetch`` and ``load`` never see
these. A scene is read as a window of a cloud-optimised GeoTIFF, which an
adapter's ``plan`` of whole artefacts cannot express, and nothing here becomes
a row: what is read is drawn, never loaded (D-133). GIBS keeps its registered
descriptor, because the tiles it serves are the same tiles either way.

**Which layer says cloud.** Each searchable source names a classification
asset, the product's own per-pixel verdict, and the cloud fraction over an area
is counted from that and nothing else (D-372). GIBS publishes no such layer,
so its daily view states no cloud fraction rather than one guessed from the
picture.

Reference: docs/DECISIONS.md D-133, D-134, D-220, D-371, D-372.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from meridian_ingest.adapters.protocol import SourceDescriptor
from meridian_ingest.adapters.public.gibs import GIBS
from meridian_ingest.imagery.model import SourceClass

__all__ = [
    "GIBS_CREDIT",
    "GIBS_LEVEL",
    "GIBS_SOURCE",
    "IMAGERY_SOURCES",
    "LANDSAT",
    "LANDSAT_SOURCE",
    "LANDSAT_WHITE",
    "SENTINEL2",
    "SENTINEL2_SOURCE",
    "GibsSource",
    "ImagerySource",
    "StacSource",
    "UnknownImagerySourceError",
    "credit_for",
    "imagery_source",
]

SENTINEL2 = SourceDescriptor(
    source_id="sentinel2_l2a",
    source_class="imagery",
    name="Copernicus Sentinel-2 L2A scenes on Earth Search (rendered for display)",
    licence=(
        "Copernicus free, full and open; adapted data carries "
        "'Contains modified Copernicus Sentinel data [Year]'"
    ),
    terms_url=(
        "https://sentinels.copernicus.eu/documents/247904/690755/"
        "Sentinel_Data_Legal_Notice"
    ),
    attribution_entry="Copernicus Sentinel-2 L2A on Earth Search",
    access_constraint="none",
)

LANDSAT = SourceDescriptor(
    source_id="landsat_c2_l2",
    source_class="imagery",
    name=(
        "USGS Landsat Collection 2 Level-2 scenes via Microsoft Planetary "
        "Computer (rendered for display)"
    ),
    licence="Public domain (USGS); reached under Planetary Computer's terms",
    terms_url="https://planetarycomputer.microsoft.com/terms",
    attribution_entry=(
        "USGS Landsat Collection 2 Level-2 via Microsoft Planetary Computer"
    ),
    access_constraint="none",
)


Colour = Literal["as_published", "surface_reflectance"]


@dataclass(frozen=True, slots=True)
class StacSource:
    """An archive searched through a STAC API and read by windows of its COGs.

    ``display_assets`` are drawn, in band order; ``classification_asset`` is
    counted. ``platforms``, when not empty, is the only platforms a scene may
    come from.
    """

    descriptor: SourceDescriptor
    source_class: SourceClass
    search_url: str
    collection: str
    lookback_days: int
    display_assets: tuple[str, ...]
    classification_asset: str
    classifier: str
    """How the classification asset is read: ``scl`` or ``qa_pixel``."""

    instrument: str
    rendering: str
    """How its display assets become a true-colour picture, as a manifest says."""

    credit: str
    """The credit every file rendered from it carries; ``{year}`` is the
    acquisition year."""

    token_url: str | None = None
    """Where an anonymous read token is asked for, if the archive needs one.
    The token is held in memory for one run and never written (D-372)."""

    platforms: tuple[str, ...] = ()
    colour: Colour = "as_published"
    """``as_published`` when the display assets are already a picture;
    ``surface_reflectance`` when they are reflectance, stretched to one."""

    @property
    def source_id(self) -> str:
        """The source's id, which is also its directory in the raw store."""
        return self.descriptor.source_id


@dataclass(frozen=True, slots=True)
class GibsSource:
    """GIBS's daily true-colour view, read as tiles at the level nearest 250 m."""

    descriptor: SourceDescriptor
    source_class: SourceClass
    layer: str
    level: int
    platform: str
    instrument: str
    rendering: str
    credit: str

    @property
    def source_id(self) -> str:
        """The source's id, which is also its directory in the raw store."""
        return self.descriptor.source_id


ImagerySource = StacSource | GibsSource

SENTINEL2_SOURCE = StacSource(
    descriptor=SENTINEL2,
    source_class="high_resolution",
    search_url="https://earth-search.aws.element84.com/v1/search",
    collection="sentinel-2-l2a",
    lookback_days=30,
    display_assets=("visual",),
    classification_asset="scl",
    classifier="scl",
    instrument="MSI",
    rendering=(
        "the visual asset (true colour from B04, B03 and B02, 8-bit) as "
        "published, reprojected to EPSG:4326 by bilinear resampling"
    ),
    credit="Contains modified Copernicus Sentinel data {year}",
)

LANDSAT_WHITE = 0.25
"""The surface reflectance drawn as full brightness in a Landsat picture. Most
land lies well under it, so fields and streets keep their detail; cloud and
bright roofs above it are clipped to white."""

LANDSAT_SOURCE = StacSource(
    descriptor=LANDSAT,
    source_class="older_record",
    search_url="https://planetarycomputer.microsoft.com/api/stac/v1/search",
    collection="landsat-c2-l2",
    lookback_days=90,
    display_assets=("red", "green", "blue"),
    classification_asset="qa_pixel",
    classifier="qa_pixel",
    instrument="OLI",
    rendering=(
        "surface reflectance from the red, green and blue assets (SR_B4, SR_B3, "
        "SR_B2), scaled by 0.0000275 and offset by -0.2 as the product states, "
        f"reflectance 0 to {LANDSAT_WHITE} mapped linearly to 0-255 and clipped "
        "above, reprojected to EPSG:4326 by bilinear resampling"
    ),
    credit="Landsat imagery courtesy of the U.S. Geological Survey",
    token_url="https://planetarycomputer.microsoft.com/api/sas/v1/token/landsat-c2-l2",
    platforms=("landsat-8", "landsat-9"),
    colour="surface_reflectance",
)
"""Landsat 8 and 9 only. Landsat 7's scan-line corrector failed in 2003, and
its scenes since carry stripes of missing data across any area."""

GIBS_LEVEL = 8
"""In GIBS's geographic ``250m`` tile matrix set, a level-8 tile is 1.125° on
512 pixels: about 244 m a pixel at the equator, the level nearest 250 m."""

GIBS_CREDIT = (
    "We acknowledge the use of imagery provided by services from NASA's Global "
    "Imagery Browse Services (GIBS), part of NASA's Earth Science Data and "
    "Information System (ESDIS)."
)
"""GIBS's acknowledgement, as its page gave it on 2026-11-14 (ATTRIBUTION.md)."""

GIBS_SOURCE = GibsSource(
    descriptor=GIBS,
    source_class="daily_wide",
    layer="VIIRS_NOAA20_CorrectedReflectance_TrueColor",
    level=GIBS_LEVEL,
    platform="NOAA-20",
    instrument="VIIRS",
    rendering=(
        "its corrected-reflectance true-colour tiles as published, placed side "
        "by side on their own EPSG:4326 grid and cut to the area's box, not "
        "resampled"
    ),
    credit=GIBS_CREDIT,
)
"""NOAA-20's VIIRS rather than the Terra MODIS layer the display adapter uses.
Terra launched in 1999 on a six-year design life, and a daily view published
every day should come from a satellite in its operational life. Part 9's live
fetch confirms the layer is served at this level."""

IMAGERY_SOURCES: dict[str, ImagerySource] = {
    one.source_id: one for one in (SENTINEL2_SOURCE, LANDSAT_SOURCE, GIBS_SOURCE)
}


def credit_for(source: ImagerySource, year: int) -> str:
    """The credit line for a picture acquired in ``year``."""
    return source.credit.format(year=year)


class UnknownImagerySourceError(LookupError):
    """A source id no imagery source is described under."""


def imagery_source(source_id: str) -> ImagerySource:
    """The imagery source described under one id.

    Raises:
        UnknownImagerySourceError: None is, naming the ones that are.
    """
    try:
        return IMAGERY_SOURCES[source_id]
    except KeyError:
        known = ", ".join(sorted(IMAGERY_SOURCES))
        message = f"no imagery source is {source_id!r}; imagery sources: {known}"
        raise UnknownImagerySourceError(message) from None
