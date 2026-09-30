"""What every Stage 31 adapter and normaliser shares.

Small on purpose: a helper here is one every source genuinely needs, not a
framework the sources are bent to fit. Each normaliser still reads its own
format in its own module, because a format is what a source owns.

Reference: docs/DECISIONS.md D-131, D-141, D-142, D-221, D-222.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, date, datetime, timedelta

from meridian_ingest.adapters.protocol import FetchRequest, require_source_version
from meridian_ingest.extent import BoundingBox
from meridian_ingest.normalise.records import NormalisationError
from meridian_ingest.raw_store import StoredArtefact
from meridian_ingest.retrieval import RetrievedArtefact

__all__ = [
    "days_between",
    "missing_unless",
    "number_or_none",
    "parse_json",
    "require_bbox",
    "require_interval",
    "require_points",
    "retrieved_at",
    "utc",
    "version_from_headers",
]


def version_from_headers(retrieved: RetrievedArtefact) -> str:
    """A live product's version, from whichever header the server gives.

    Args:
        retrieved: The response.

    Returns:
        The ``ETag``, else the ``Last-Modified``, else the server's own
        ``Date`` marked as such. A product computed on request — a forecast, a
        subset — has no entity tag, and the instant the server says it served
        it is the only honest name for which version we took.

    Raises:
        ValueError: The response carried none of the three.
    """
    headers = {name.lower(): value for name, value in retrieved.headers.items()}
    for name in ("etag", "last-modified"):
        if headers.get(name, "").strip():
            return require_source_version(headers[name], retrieved.remote)
    served = headers.get("date", "").strip()
    version = f"served {served}" if served else ""
    return require_source_version(version, retrieved.remote)


def parse_json(artefact: StoredArtefact) -> object:
    """The artefact's bytes as JSON, or a refusal naming it."""
    try:
        return json.loads(artefact.read_bytes().decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        message = f"{artefact.raw_path} is not readable JSON: {exc}"
        raise NormalisationError(message) from exc


def retrieved_at(artefact: StoredArtefact) -> datetime:
    """When we fetched it — from its manifest, never from a clock (D-141)."""
    return artefact.manifest.provenance.retrieved_at


def utc(text: object) -> datetime:
    """One source timestamp without an offset, read as the UTC it is documented as.

    Every source in this package documents its times as UTC and most omit the
    offset. A value that *does* carry one is converted rather than trusted to
    be zero.
    """
    if not isinstance(text, str) or not text.strip():
        message = f"a timestamp must be text, not {text!r}"
        raise NormalisationError(message)
    try:
        parsed = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        message = f"{text!r} is not an ISO 8601 instant"
        raise NormalisationError(message) from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def number_or_none(value: object, fill: float | None = None) -> float | None:
    """A published number, or None where the source published none.

    Args:
        value: As parsed. ``None``, an empty string, NaN and the source's own
            fill value are all "nothing was published".
        fill: The source's documented fill value, if it has one.

    Raises:
        NormalisationError: The value is present and not a number.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        message = f"{value!r} is a flag, not a number"
        raise NormalisationError(message)
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        message = f"{value!r} is not a number"
        raise NormalisationError(message) from exc
    if not math.isfinite(number) or (fill is not None and number == fill):
        return None
    return number


def missing_unless(value: float | None, reason: str) -> str | None:
    """The reason a value is missing, or None when it is there (D-221)."""
    return None if value is not None else reason


def require_points(request: FetchRequest, source_id: str) -> None:
    """Refuse to plan a point product nobody said where to ask about."""
    if not request.points:
        message = f"{source_id} is asked about places: set [sources.{source_id}] points"
        raise ValueError(message)


def require_bbox(request: FetchRequest, source_id: str) -> BoundingBox:
    """The box an area product is asked about, or a refusal naming the setting."""
    if request.bbox is None:
        message = f"{source_id} is asked about an area: set [sources.{source_id}] bbox"
        raise ValueError(message)
    return request.bbox


def require_interval(
    request: FetchRequest, source_id: str
) -> tuple[datetime, datetime]:
    """The interval a dated product is asked for, both ends required.

    A dated product has no "latest" to fall back on, and inventing an interval
    from a clock inside an adapter would make planning depend on when it ran;
    ``meridian-ingest follow`` supplies both ends from its own clock instead.
    """
    if request.since is None or request.until is None:
        message = f"{source_id} is dated: give both --since and --until"
        raise ValueError(message)
    return request.since, request.until


def days_between(since: datetime, until: datetime) -> list[date]:
    """Each UTC day that ``[since, until)`` touches, in order."""
    first = since.astimezone(UTC).date()
    last = (until.astimezone(UTC) - timedelta(microseconds=1)).date()
    return [first + timedelta(days=n) for n in range((last - first).days + 1)]
