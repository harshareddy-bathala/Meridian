"""Orbital elements: current GP element sets, as CelesTrak publishes them.

**What it is for.** Every pass Meridian predicts is propagated from an element
set, and a set's accuracy decays with its age. Until Stage 34 the sets came
from a file an operator put on disk, so a deployment's predictions aged from
the day the file was written. This source keeps them current (D-320).

**The platform never fetches.** This adapter runs where ``meridian-ingest`` is
installed, never in the platform image (D-138, D-225). What it loads lands in
``element_sets`` with source ``celestrak``; pass generation reads the newest
set current at each instant, and a round with every source down schedules from
the last one stored.

**Only what the catalogue tracks is loaded.** A group holds far more objects
than a deployment tracks. The loader stores a set only for a satellite already
in ``satellites`` and counts the rest, so a public catalogue never decides what
is propagated.

**Format.** Plain text, each object as a name line and the two element-set
lines, with no blank line between objects (``FORMAT=TLE``). A query is either
a named group, fetched whole in one request, or one catalogue number. The
two-line format cannot carry a six-digit catalogue number, which no object
this project receives has.

**Politeness.** CelesTrak checks for new data once every two hours and asks
that each be downloaded once per update. It answers a repeated download with
a 403, and firewalls an address after 50 errors in two hours. So the cadence
is six hours, a group is preferred over many single numbers, and a 403 is never
asked again (``politeness.RETRYABLE_STATUSES``).

Reference: docs/DECISIONS.md D-079, D-138, D-225, D-320;
https://celestrak.org/NORAD/documentation/gp-data-formats.php,
https://celestrak.org/usage-policy.php.
"""

from __future__ import annotations

import re
from urllib.parse import quote

from meridian_ingest.adapters.protocol import (
    FetchRequest,
    SourceDescriptor,
    refuse_a_tile,
)
from meridian_ingest.adapters.public.common import version_from_headers
from meridian_ingest.normalise.records import (
    NormalisationError,
    NormalisedBatch,
    NormalisedElementSet,
)
from meridian_ingest.raw_store import StoredArtefact
from meridian_ingest.retrieval import RemoteArtefact, RetrievedArtefact

__all__ = [
    "CELESTRAK_GP",
    "ELEMENT_SET_SOURCES",
    "TRANSFORMATION_VERSION",
    "CelestrakAdapter",
    "CelestrakNormaliser",
]

CELESTRAK_GP = SourceDescriptor(
    source_id="celestrak_gp",
    source_class="orbital_elements",
    name="CelesTrak GP element sets",
    licence=(
        "No licence published; CelesTrak states it makes GP data freely available "
        "to all users. The data originates with the US Space Force (18 SDS) "
        "through Space-Track"
    ),
    terms_url="https://celestrak.org/usage-policy.php",
    attribution_entry="CelesTrak GP element sets",
    access_constraint="none",
)

ELEMENT_SET_SOURCES = {CELESTRAK_GP.source_id: "celestrak"}
"""The ``element_sets.source`` value each element-set source loads under.

``element_sets`` records provenance in ``source`` (D-049), whose vocabulary is
older than ingest and names catalogues, not our source ids.
"""

BASE_URL = "https://celestrak.org/NORAD/elements/gp.php"
TRANSFORMATION_VERSION = "celestrak-tle-1"

_GROUP = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
_NO_DATA = "No GP data found"


class CelestrakAdapter:
    """One artefact per named group, then one per catalogue number."""

    @property
    def descriptor(self) -> SourceDescriptor:
        """What this source is, and what it may be used under."""
        return CELESTRAK_GP

    def plan(self, request: FetchRequest) -> tuple[RemoteArtefact, ...]:
        """The groups and numbers the settings file names, in that order.

        The product is current state, so ``since`` and ``until`` select
        nothing; an older set is in the archive only if it was fetched then.

        Raises:
            ValueError: Neither groups nor catalogue numbers are set, a group
                name is not one CelesTrak could have, or more artefacts are
                named than the request allows. A fetch is never silently cut
                short: the objects left out would simply age.
        """
        if not request.groups and not request.catalog_numbers:
            message = (
                f"{CELESTRAK_GP.source_id} is asked about objects: set "
                f"[sources.{CELESTRAK_GP.source_id}] groups or catalog_numbers"
            )
            raise ValueError(message)
        for group in request.groups:
            if not _GROUP.fullmatch(group):
                message = f"group {group!r} is not a CelesTrak group name"
                raise ValueError(message)
        planned = (
            *(_remote("GROUP", group) for group in request.groups),
            *(_remote("CATNR", str(number)) for number in request.catalog_numbers),
        )
        if len(planned) > request.limit:
            message = (
                f"{len(planned)} queries are named and the limit is {request.limit}; "
                "raise request_budget or name a group instead of single numbers"
            )
            raise ValueError(message)
        return planned

    def source_version(self, retrieved: RetrievedArtefact) -> str:
        """The server's validator for this response, or when it served it."""
        return version_from_headers(retrieved)


class CelestrakNormaliser:
    """A ``FORMAT=TLE`` response as element sets, one per object."""

    transformation_version = TRANSFORMATION_VERSION

    def normalise(self, artefact: StoredArtefact) -> NormalisedBatch:
        """Every object the response lists, each read and checked.

        Raises:
            NormalisationError: The text is not a sequence of element sets, or
                one of them fails its checksum or names another object.
        """
        refuse_a_tile(artefact.manifest.provenance)
        try:
            text = artefact.read_bytes().decode("ascii")
        except UnicodeDecodeError as exc:
            message = f"{artefact.raw_path} is not ASCII text: {exc}"
            raise NormalisationError(message) from exc
        lines = [line.rstrip() for line in text.splitlines() if line.strip()]
        if lines == [_NO_DATA]:
            return NormalisedBatch(transformation_version=self.transformation_version)
        return NormalisedBatch(
            transformation_version=self.transformation_version,
            element_sets=tuple(_element_sets(lines, artefact.raw_path)),
        )


def _remote(query: str, value: str) -> RemoteArtefact:
    """One query, named by what it asks for. Never a path element (D-141)."""
    return RemoteArtefact(
        url=f"{BASE_URL}?{query}={quote(value, safe='')}&FORMAT=TLE",
        original_identifier=f"{query}={value}",
        payload_kind="data",
    )


def _element_sets(lines: list[str], where: str) -> list[NormalisedElementSet]:
    """Name lines and line pairs into element sets, with or without the names."""
    sets = []
    index = 0
    while index < len(lines):
        name = None
        if not lines[index].startswith("1 "):
            name = lines[index].strip()
            index += 1
        if index + 1 >= len(lines) or not (
            lines[index].startswith("1 ") and lines[index + 1].startswith("2 ")
        ):
            message = f"{where}: line {index + 1} does not start an element set"
            raise NormalisationError(message)
        line1, line2 = lines[index], lines[index + 1]
        catalogue = line1[2:7].strip()
        if not catalogue.isdigit():
            message = (
                f"{where}: line {index + 1} names catalogue number {catalogue!r}, "
                "which is not a number"
            )
            raise NormalisationError(message)
        sets.append(
            NormalisedElementSet(
                satellite_key=f"norad:{int(catalogue)}",
                line1=line1,
                line2=line2,
                name=name,
            )
        )
        index += 2
    return sets
