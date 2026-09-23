"""The reference archive: a source of our own, so the stage can be demonstrated.

An :class:`~meridian_ingest.adapters.protocol.Adapter` and a
:class:`~meridian_ingest.adapters.protocol.Normaliser` over synthetic artefacts
written for the purpose and shipped beside this module in ``reference_data/``.
Nothing here is a recording of a real archive — a recorded fixture would be that
archive's data committed to a public repository, which is the redistribution
question D-136 leaves open, and a commit about test plumbing is not where that
gets settled (D-142).

**It is a real source, not a test double.** It is registered in
``ingest_sources`` like any other, fetched from through the same path, and
published under this repository's own licence. So the completion gate —
downloaded once, then normalised repeatedly with nothing reachable — is
demonstrable by anyone who installs the distribution, rather than only by
someone with a checkout and the test suite.

**The URLs it plans are under ``.invalid``**, a suffix reserved never to
resolve. The adapter is written exactly as one against a real archive would be,
and only the retriever underneath it differs; pointing a real one at these URLs
still reaches nothing.

Reference: docs/DECISIONS.md D-133, D-134, D-136, D-139, D-142.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from meridian_ingest.adapters.protocol import (
    FetchRequest,
    SourceDescriptor,
    refuse_a_tile,
    require_source_version,
)
from meridian_ingest.normalise.records import (
    NormalisationError,
    NormalisedBatch,
    NormalisedReception,
    NormalisedStation,
)
from meridian_ingest.raw_store import StoredArtefact
from meridian_ingest.retrieval import RemoteArtefact, RetrievedArtefact

__all__ = [
    "BASE_URL",
    "FIXTURE_ROOT",
    "OUTCOMES",
    "REFERENCE_SOURCE",
    "SCHEMA",
    "TRANSFORMATION_VERSION",
    "ReferenceAdapter",
    "ReferenceNormaliser",
]

REFERENCE_SOURCE = SourceDescriptor(
    source_id="reference_archive",
    source_class="archive_receptions",
    name="Meridian reference archive",
    licence="Apache-2.0",
    terms_url="https://www.apache.org/licenses/LICENSE-2.0",
    attribution_entry="The reference adapter's fixtures are ours",
    access_constraint="none",
)
"""Registered like any other source, and the terms are genuinely ours.

The licence is this repository's, because the artefacts are this repository's.
``attribution_entry`` names the heading in ``ATTRIBUTION.md`` verbatim, which
is what ``fetch`` looks for before it opens a socket (D-134).
"""

BASE_URL = "https://reference-archive.invalid/v1/"
FIXTURE_ROOT = Path(__file__).parent / "reference_data"
SCHEMA = "reference-archive/1"
TRANSFORMATION_VERSION = "reference-1"
"""Bumped when this normaliser's output changes.

A module constant rather than a timestamp or a commit hash: it is part of the
stored key, so a second normalisation appends a row beside the first and the
two can be compared. A version that drifted every run would make that
comparison meaningless (D-140).
"""

OUTCOMES = {
    "decoded": "decoded",
    "partial": "signal_no_decode",
    "nothing": "no_data",
}
"""This archive's words, mapped to ours. Anything else becomes ``unknown``.

``nothing`` maps to ``no_data`` and **never** to ``no_signal``: ``no_signal``
asserts that a station was verifiably listening, and no heartbeat exists for
somebody else's station (rule 7, D-139). ``source_outcome`` keeps the archive's
own string either way, so this table is checkable by somebody who doubts it.
"""


@dataclass(frozen=True, slots=True)
class _Published:
    """One artefact the archive publishes, as its listing would describe it."""

    filename: str
    payload_kind: str
    valid_from: datetime
    valid_to: datetime


CATALOGUE = (
    _Published(
        "receptions-2026-07.json",
        "data",
        datetime(2026, 7, 1, tzinfo=UTC),
        datetime(2026, 8, 1, tzinfo=UTC),
    ),
    _Published(
        "receptions-2026-08.json",
        "data",
        datetime(2026, 8, 1, tzinfo=UTC),
        datetime(2026, 9, 1, tzinfo=UTC),
    ),
    _Published(
        "coverage-2026-08.png",
        "tile",
        datetime(2026, 8, 1, tzinfo=UTC),
        datetime(2026, 9, 1, tzinfo=UTC),
    ),
)
"""What the archive offers. A real adapter reads this from a listing endpoint;
here it is a literal, which is the only shortcut this module takes."""


class ReferenceAdapter:
    """The reference archive, as the fetch path sees it.

    Args:
        base_url: The archive's root. Overridable only so a test can prove the
            adapter plans against whatever it is pointed at rather than a
            hard-coded string.
    """

    def __init__(self, base_url: str = BASE_URL) -> None:
        """Point at ``base_url``. Nothing is fetched or read."""
        self._base_url = base_url

    @property
    def descriptor(self) -> SourceDescriptor:
        """What this source is, and what it may be used under."""
        return REFERENCE_SOURCE

    def plan(self, request: FetchRequest) -> tuple[RemoteArtefact, ...]:
        """Which artefacts cover the requested interval.

        Args:
            request: The interval and the ceiling an operator gave.

        Returns:
            At most ``request.limit`` artefacts, in publication order. Names
            only — nothing here opens a socket.

        Note:
            An artefact is included when the month it covers *overlaps* the
            request, not when it is contained by it. A request for one day in
            August wants August's file; requiring containment would silently
            return nothing and look like an archive with no data.

            Both intervals are half-open — see :func:`_overlaps`.
        """
        wanted = [
            self._remote(published)
            for published in CATALOGUE
            if _overlaps(published, request)
        ]
        return tuple(wanted[: request.limit])

    def source_version(self, retrieved: RetrievedArtefact) -> str:
        """The archive's own version of one artefact, from its ``ETag``.

        Args:
            retrieved: The response.

        Returns:
            The entity tag, stripped.

        Raises:
            ValueError: The response carried no version, which refuses the
                retrieval before anything is published.
        """
        return require_source_version(
            retrieved.headers.get("ETag", ""), retrieved.remote
        )

    def _remote(self, published: _Published) -> RemoteArtefact:
        """One catalogue entry as something to ask for."""
        return RemoteArtefact(
            url=f"{self._base_url}{published.filename}",
            original_identifier=published.filename,
            payload_kind=published.payload_kind,
            valid_from=published.valid_from,
            valid_to=published.valid_to,
        )


class ReferenceNormaliser:
    """The reference archive's format, as the offline path sees it.

    Takes a stored artefact and nothing else. There is no retriever here, no
    URL and no clock, so this class could not reach the archive it normalises
    even if it tried — which is the whole of D-142's guarantee, and the reason
    the completion gate is a demonstration rather than a claim.
    """

    transformation_version = TRANSFORMATION_VERSION

    def normalise(self, artefact: StoredArtefact) -> NormalisedBatch:
        """Turn one stored artefact into stations and receptions.

        Args:
            artefact: The bytes and the manifest, read from the raw store.

        Returns:
            Everything the artefact describes, under
            :data:`TRANSFORMATION_VERSION`.

        Raises:
            TileIsNotAQuantityError: The artefact is a tile. Checked first, so
                no code path exists that could derive a number from one
                (D-133).
            NormalisationError: The bytes are not this archive's format, or
                describe something the schema cannot hold.
        """
        refuse_a_tile(artefact.manifest.provenance)
        payload = _parse(artefact)
        try:
            stations = tuple(map(_station, _entries(payload, "stations")))
            receptions = tuple(map(_reception, _entries(payload, "receptions")))
        except (KeyError, TypeError, AttributeError) as exc:
            message = f"{artefact.raw_path} is missing a field it needs: {exc!r}"
            raise NormalisationError(message) from exc
        return NormalisedBatch(
            transformation_version=self.transformation_version,
            stations=stations,
            receptions=receptions,
        )


def _overlaps(published: _Published, request: FetchRequest) -> bool:
    """Whether one published artefact's coverage meets the requested interval.

    Both intervals are **half-open**, ``[from, to)``. July's file is published
    as 2026-07-01 to 2026-08-01, which means "July" and not "July and the first
    instant of August" — so a request beginning exactly at 2026-08-01 wants
    August's file and not July's. Writing the closed form instead
    (``2026-07-31T23:59:59.999Z``) would make the boundary depend on how many
    decimal places somebody chose.
    """
    if request.until is not None and published.valid_from >= request.until:
        return False
    return not (request.since is not None and published.valid_to <= request.since)


def _parse(artefact: StoredArtefact) -> dict[str, object]:
    """The artefact's bytes as this archive's document, or a refusal."""
    try:
        decoded = json.loads(artefact.read_bytes().decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        message = f"{artefact.raw_path} is not readable JSON: {exc}"
        raise NormalisationError(message) from exc
    if not isinstance(decoded, dict) or decoded.get("schema") != SCHEMA:
        message = (
            f"{artefact.raw_path} is not {SCHEMA}; a normaliser is chosen by the "
            "adapter, so this one being handed something else is a wiring mistake"
        )
        raise NormalisationError(message)
    return decoded


def _entries(payload: dict[str, object], key: str) -> list[dict[str, object]]:
    """One of the document's two arrays, as mappings.

    Narrowed here rather than trusted, because everything below this line
    indexes these entries by name and a list of strings would fail somewhere
    less informative.
    """
    value = payload.get(key)
    if not isinstance(value, list):
        message = f"{key} is {type(value).__name__}, not a list"
        raise NormalisationError(message)
    entries = []
    for index, entry in enumerate(value):
        if not isinstance(entry, dict):
            message = f"{key}[{index}] is {type(entry).__name__}, not an object"
            raise NormalisationError(message)
        entries.append({str(name): item for name, item in entry.items()})
    return entries


def _station(entry: dict[str, object]) -> NormalisedStation:
    """One station description, in our shape."""
    return NormalisedStation(
        source_station_key=str(entry["key"]),
        name=_optional_text(entry["name"]),
        lat_deg=_optional_number(entry["lat"]),
        lon_deg=_optional_number(entry["lon"]),
        alt_m=_optional_number(entry["alt_m"]),
        capability=_optional_mapping(entry["capability"]),
    )


def _reception(entry: dict[str, object]) -> NormalisedReception:
    """One reception, in our shape, keeping the archive's own words beside ours."""
    key, kind = _object_key(entry["object"])
    result = str(entry["result"])
    ended = entry["end"]
    return NormalisedReception(
        source_observation_id=str(entry["id"]),
        satellite_key=key,
        satellite_key_kind=kind,
        started_at=_instant(entry["start"]),
        archive_outcome=OUTCOMES.get(result, "unknown"),
        source_outcome=result,
        source_station_key=_optional_text(entry["station"]),
        ended_at=None if ended is None else _instant(ended),
        max_elevation_deg=_optional_number(entry["max_elevation"]),
        centre_freq_hz=_optional_whole(entry["frequency_hz"]),
        mode=_optional_text(entry["mode"]),
        peak_snr_db=_optional_number(entry["snr_db"]),
        frames_decoded=_optional_whole(entry["frames"]),
    )


def _object_key(described: object) -> tuple[str, str]:
    """How confidently this archive identified an object, and under what key.

    Namespaced rather than bare, so two archives' identifier spaces cannot
    collide in one text column, and an unresolved name stays visibly
    unresolved instead of looking like a catalogue number nobody matched.
    """
    if not isinstance(described, dict):
        message = f"an object must be described by a mapping, not {described!r}"
        raise NormalisationError(message)
    if "norad" in described:
        return f"norad:{int(described['norad'])}", "norad"
    if "intl" in described:
        return f"intl:{described['intl']}", "international_designator"
    if "name" in described:
        return f"name:{described['name']}", "source_name"
    message = f"an object described by none of norad, intl or name: {described!r}"
    raise NormalisationError(message)


def _instant(value: object) -> datetime:
    """One of this archive's timestamps, which are always ``Z``."""
    if not isinstance(value, str):
        message = f"a timestamp must be a string, not {value!r}"
        raise NormalisationError(message)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        message = f"{value!r} is not an ISO 8601 instant"
        raise NormalisationError(message) from exc


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_number(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        message = f"{value!r} is not a number"
        raise NormalisationError(message)
    return float(value)


def _optional_mapping(value: object) -> dict[str, object] | None:
    """A declared capability, in whatever shape the archive publishes it."""
    if value is None:
        return None
    if not isinstance(value, dict):
        message = f"a capability must be a mapping, not {value!r}"
        raise NormalisationError(message)
    return {str(name): item for name, item in value.items()}


def _optional_whole(value: object) -> int | None:
    """A count or a frequency: whole, or absent, never rounded into being."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        message = f"{value!r} is not a whole number"
        raise NormalisationError(message)
    return value
