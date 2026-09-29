"""India's national geoportal: ISRO's Bhuvan, map images for display only.

**Why display only.** Bhuvan's terms of service, as we could read them, say
that its image data, map data and related content are provided *for viewing
purposes only*, and grant no ownership or other use unless NRSC permits it
specifically. So this adapter obtains map images of the layers an operator
names, over the configured box, to show behind an Indian area of interest —
and its normaliser derives nothing, whatever the image shows. A regional
*data* product for India is a separate source, adopted once its terms permit
derivation (D-220).

**Format.** OGC WMS 1.1.1 ``GetMap`` from Bhuvan's GeoServer, PNG, in
geographic coordinates. Layer names are Bhuvan's own (for example the
state-wise land use and land cover layers), set in the settings file, because
choosing what to show is the operator's decision and not the adapter's.

Reference: docs/DECISIONS.md D-133, D-134, D-220.
"""

from __future__ import annotations

from urllib.parse import quote

from meridian_ingest.adapters.protocol import FetchRequest, SourceDescriptor
from meridian_ingest.adapters.public.common import require_bbox, version_from_headers
from meridian_ingest.retrieval import RemoteArtefact, RetrievedArtefact

__all__ = ["BHUVAN", "BhuvanAdapter"]

BHUVAN = SourceDescriptor(
    source_id="isro_bhuvan",
    source_class="imagery",
    name="ISRO Bhuvan geoportal WMS map images (display only)",
    licence="DOS/ISRO/NRSC non-exclusive licence; content for viewing purposes only",
    terms_url="https://bhuvan.nrsc.gov.in/wiki/index.php/Information_for_Users",
    attribution_entry="ISRO Bhuvan geoportal",
    access_constraint="none",
)

BASE = "https://bhuvan-vec2.nrsc.gov.in/bhuvan/wms"
SIZE_PX = 512


class BhuvanAdapter:
    """One map image per configured layer over the configured box."""

    @property
    def descriptor(self) -> SourceDescriptor:
        """What this source is, and what it may be used under."""
        return BHUVAN

    def plan(self, request: FetchRequest) -> tuple[RemoteArtefact, ...]:
        """Each named layer, as a tile.

        Raises:
            ValueError: No box, or no layer, is configured. There is no default
                layer: which of Bhuvan's is shown is the operator's choice.
        """
        bbox = require_bbox(request, BHUVAN.source_id)
        if not request.layers:
            message = "isro_bhuvan shows named layers: set [sources.isro_bhuvan] layers"
            raise ValueError(message)
        planned = []
        for layer in request.layers[: request.limit]:
            query = (
                "service=WMS&version=1.1.1&request=GetMap"
                f"&layers={quote(layer, safe=':_')}&styles=&srs=EPSG:4326"
                f"&bbox={bbox.as_text()}&width={SIZE_PX}&height={SIZE_PX}"
                "&format=image/png&transparent=true"
            )
            planned.append(
                RemoteArtefact(
                    url=f"{BASE}?{query}",
                    original_identifier=f"{layer}@{bbox.as_text()}",
                    payload_kind="tile",
                    spatial_extent={
                        "west": bbox.west,
                        "south": bbox.south,
                        "east": bbox.east,
                        "north": bbox.north,
                    },
                )
            )
        return tuple(planned)

    def source_version(self, retrieved: RetrievedArtefact) -> str:
        """The map server's validator, or when it served the image."""
        return version_from_headers(retrieved)
