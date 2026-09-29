"""Space weather: the planetary K index, as NOAA SWPC publishes it in near real time.

**What it measures.** Kp summarises geomagnetic disturbance over three hours on
a quasi-logarithmic 0–9 scale, from a network of ground magnetometers. At
137 MHz the ionosphere is mostly transparent, but a disturbed one raises
absorption and scintillation, which moves the noise floor and the decode rate
for reasons that have nothing to do with the station (D-131). Kp is the
candidate feature for that.

**What is published when.** SWPC's product is an *estimate*, recomputed as
magnetometer data arrives, over a rolling week. The definitive index follows
weeks later from GFZ Potsdam. So the same three-hour interval is published
several times with different values, and each fetch that differs is a new
artefact whose rows carry a later ``published_at`` — the pre-pass rule then
reads the estimate that existed before the pass, never the revision (D-222).

**Format.** A JSON array. The 2026 shape is one object per interval with
``time_tag`` (the interval's start, UTC, no offset), ``Kp``, ``a_running`` and
``station_count``; the earlier shape was an array of arrays under a header row.
Both are read, because a change of shape upstream should not silently empty a
feature.

Reference: docs/DECISIONS.md D-131, D-220, D-222.
"""

from __future__ import annotations

from datetime import timedelta

from meridian_ingest.adapters.protocol import (
    FetchRequest,
    SourceDescriptor,
    refuse_a_tile,
)
from meridian_ingest.adapters.public.common import (
    missing_unless,
    number_or_none,
    parse_json,
    retrieved_at,
    utc,
    version_from_headers,
)
from meridian_ingest.normalise.records import NormalisationError, NormalisedBatch
from meridian_ingest.normalise.samples import NormalisedSample, published_bound
from meridian_ingest.raw_store import StoredArtefact
from meridian_ingest.retrieval import RemoteArtefact, RetrievedArtefact

__all__ = ["SWPC_KP", "TRANSFORMATION_VERSION", "SwpcKpAdapter", "SwpcKpNormaliser"]

SWPC_KP = SourceDescriptor(
    source_id="noaa_swpc_kp",
    source_class="space_weather",
    name="NOAA SWPC planetary K index (estimated)",
    licence="US Government work; SWPC states no copyright or other restrictions",
    terms_url="https://www.swpc.noaa.gov/disclaimer",
    attribution_entry="NOAA SWPC planetary K index",
    access_constraint="none",
)

URL = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"
TRANSFORMATION_VERSION = "swpc-kp-1"
PRODUCT = "SWPC noaa-planetary-k-index (estimated Kp)"
INTERVAL = timedelta(hours=3)


class SwpcKpAdapter:
    """The rolling K index product: one artefact, whatever the interval asked."""

    @property
    def descriptor(self) -> SourceDescriptor:
        """What this source is, and what it may be used under."""
        return SWPC_KP

    def plan(self, request: FetchRequest) -> tuple[RemoteArtefact, ...]:
        """The one current file.

        The product is a rolling week and has no dated archive, so ``since``
        and ``until`` select nothing here; what they would have selected is
        whatever the week holds when it is fetched. That is why this source
        is followed on a timer rather than backfilled (D-225).
        """
        del request
        return (
            RemoteArtefact(
                url=URL,
                original_identifier="noaa-planetary-k-index.json",
                payload_kind="data",
            ),
        )

    def source_version(self, retrieved: RetrievedArtefact) -> str:
        """The server's validator for this file, or when it served it."""
        return version_from_headers(retrieved)


class SwpcKpNormaliser:
    """The K index file as samples, one per three-hour interval."""

    transformation_version = TRANSFORMATION_VERSION

    def normalise(self, artefact: StoredArtefact) -> NormalisedBatch:
        """Every interval the file lists, each published no later than we fetched it.

        Raises:
            NormalisationError: The file is neither of the two shapes.
        """
        refuse_a_tile(artefact.manifest.provenance)
        rows = _rows(parse_json(artefact), artefact.raw_path)
        published_at, basis = published_bound(None, retrieved_at(artefact))
        samples = []
        for row in rows:
            start = utc(row.get("time_tag"))
            kp = number_or_none(row.get("Kp"))
            stations = row.get("station_count")
            samples.append(
                NormalisedSample(
                    series_key=f"kp:{start.isoformat()}",
                    quantity="kp_index",
                    value_unit="Kp (0-9)",
                    observed_from=start,
                    observed_to=start + INTERVAL,
                    published_at=published_at,
                    published_basis=basis,
                    product=PRODUCT,
                    value=kp,
                    missing_reason=missing_unless(kp, "published without a value"),
                    quality=None if stations is None else f"station_count={stations}",
                )
            )
        return NormalisedBatch(
            transformation_version=self.transformation_version, samples=tuple(samples)
        )


def _rows(payload: object, where: str) -> list[dict[str, object]]:
    """The file's intervals as mappings, from either published shape."""
    if not isinstance(payload, list):
        message = f"{where} is not a JSON array"
        raise NormalisationError(message)
    if payload and isinstance(payload[0], list):
        header = [str(name) for name in payload[0]]
        return [dict(zip(header, row, strict=False)) for row in payload[1:]]
    rows = []
    for index, row in enumerate(payload):
        if not isinstance(row, dict):
            message = f"{where}[{index}] is {type(row).__name__}, not an object"
            raise NormalisationError(message)
        rows.append({str(key): value for key, value in row.items()})
    return rows
