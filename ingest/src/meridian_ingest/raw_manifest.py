"""The manifest written beside every retrieved artefact, and its canonical form.

``manifest.json`` says what the bytes next to it are: the provenance an adapter
declared, plus the digest and the length the raw store measured for itself. It
is written once, inside the directory, before that directory is renamed into
place — so a record is either complete or is not a record (D-141).

**Why a second canonical renderer exists.**
:mod:`meridian.observations.canonical_body` already renders a canonical form,
and it cannot be reused: it is typed to ``NewObservation`` and D-118 pins a
golden digest over its exact output, so widening it to arbitrary mappings would
put every stored ``observations.content_sha256`` at the mercy of an ingest
change. This one is written fresh to the same rules — sorted keys, no
whitespace, UTF-8, non-finite floats refused, instants as UTC to the
millisecond — and ``tests/unit/test_ingest_canonical_form.py`` asserts the two
agree value by value, which is the part that would actually rot.

**Instants are rendered, not guessed.** :func:`canonical_bytes` serialises only
JSON-native values and raises on anything else, rather than quietly formatting
a ``datetime`` it recognised. A renderer that repairs its input is a renderer
whose output depends on which caller reached it.

Reference: docs/DECISIONS.md D-070, D-118, D-141.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from meridian_ingest.provenance import Provenance

__all__ = [
    "MANIFEST_FORMAT",
    "MalformedManifestError",
    "RawManifest",
    "canonical_bytes",
    "canonical_sha256",
    "instant",
    "manifest_bytes",
    "parse_manifest",
]

MANIFEST_FORMAT = 1
"""Bumped when the stored shape changes.

An unknown format is refused rather than read leniently: a manifest written by
a newer writer describes bytes this one does not understand, and guessing would
put a wrong row in ``ingest_records`` instead of stopping.
"""

_DIGEST_HEX = 64
"""sha256 as hex. Hex in the file, ``bytea`` in the database — a manifest is
read by operators, and a row is not."""


class MalformedManifestError(ValueError):
    """A manifest that cannot be read back into a :class:`RawManifest`.

    Raised rather than treated as absent, for :mod:`capture_folder`'s reason:
    ignoring an unreadable manifest loses track of an artefact that was
    genuinely retrieved, and the raw store is the one thing here that cannot be
    recreated without going back to the source.
    """


@dataclass(frozen=True, slots=True)
class RawManifest:
    """One retrieved artefact, as described beside the bytes themselves."""

    provenance: Provenance
    sha256: bytes
    """Of the bytes as downloaded, before any normalisation. Exactly 32."""

    byte_count: int
    """Counted by the store while writing, never a length a source declared."""

    def __post_init__(self) -> None:
        """Refuse a digest or a length the columns could not hold."""
        if len(self.sha256) != 32:  # noqa: PLR2004 — sha256 is 32 bytes by definition
            message = f"sha256 is {len(self.sha256)} bytes, not 32"
            raise MalformedManifestError(message)
        if self.byte_count <= 0:
            message = (
                f"byte_count is {self.byte_count}; an artefact of no bytes is a "
                "fetch that failed quietly, not a record"
            )
            raise MalformedManifestError(message)


def manifest_bytes(manifest: RawManifest) -> bytes:
    """The exact bytes written to ``manifest.json``.

    Args:
        manifest: The artefact's provenance, digest and length.

    Returns:
        Canonical UTF-8 JSON with a trailing newline, so the file ends the way
        every other text file in the repository does and ``cat`` behaves.

    Note:
        Byte-identical for equal inputs, which is what lets a re-publication be
        compared with what is already stored rather than trusted.
    """
    return canonical_bytes(_to_json(manifest)) + b"\n"


def parse_manifest(raw: bytes) -> RawManifest:
    """Read ``manifest.json`` back, strictly.

    Args:
        raw: The file's bytes.

    Returns:
        The manifest it describes.

    Raises:
        MalformedManifestError: The bytes are not JSON, the format is unknown, a
            field is missing or has the wrong type, or the provenance it carries
            is incomplete.

    Note:
        **Unknown keys are refused**, unlike a permissive parser. A key this
        version does not know came from a writer that knew something more about
        the artefact, and silently dropping it would load a row that claims to
        describe bytes it only half describes.
    """
    try:
        decoded: object = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        message = f"manifest is not readable JSON: {exc}"
        raise MalformedManifestError(message) from exc

    stored = _mapping(decoded, "manifest")
    if stored.get("format") != MANIFEST_FORMAT:
        message = f"unknown manifest format {stored.get('format')!r}"
        raise MalformedManifestError(message)
    unknown = sorted(set(stored) - _FIELDS)
    if unknown:
        message = f"manifest carries fields this version does not know: {unknown}"
        raise MalformedManifestError(message)
    return _from_json(stored)


def canonical_bytes(payload: Mapping[str, object]) -> bytes:
    """Render a mapping to the one byte string a digest may be taken over.

    Args:
        payload: JSON-native values only — ``str``, ``int``, ``float``, ``bool``,
            ``None``, and mappings or sequences of them.

    Returns:
        UTF-8 JSON with sorted keys and no whitespace.

    Raises:
        MalformedManifestError: A value is not JSON-native, or a float is not
            finite. JSON has no literal for ``NaN`` or infinity, and a digest
            over a value no column can hold would be a hash of something that
            was never stored.

    Note:
        Keys are sorted so that field order never reaches the digest; arrays are
        left in their order, because an array here is a measurement and sorting
        one would make a scrambled artefact indistinguishable from an intact
        one. Both rules are :mod:`meridian.observations.canonical_body`'s, held
        to deliberately rather than by coincidence.
    """
    try:
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        message = f"value is not renderable to a canonical form: {exc}"
        raise MalformedManifestError(message) from exc


def canonical_sha256(payload: Mapping[str, object]) -> bytes:
    """The digest of :func:`canonical_bytes`, as the ``bytea`` columns hold it.

    Args:
        payload: As :func:`canonical_bytes`.

    Returns:
        Thirty-two raw bytes. Not hex: the columns are binary, and hex would
        double every stored digest for a readability nobody querying them needs.
    """
    return hashlib.sha256(canonical_bytes(payload)).digest()


def instant(value: datetime) -> str:
    """One timestamp, as UTC with millisecond precision and a ``Z`` suffix.

    Args:
        value: A timezone-aware instant.

    Returns:
        For example ``2026-09-20T10:11:43.000Z``.

    Raises:
        MalformedManifestError: The instant is naive. Hashing one as though it
            were UTC would make two fetches at different moments collide.

    Note:
        Milliseconds, so a clock with microsecond resolution and one without
        render the same artefact identically. The truncation is real and is the
        value that reaches ``ingest_records``: the manifest is the record of
        when we fetched, and a sub-millisecond fetch time is precision nobody
        has and nothing uses.
    """
    if value.tzinfo is None:
        message = "every stored instant is UTC; this one is naive"
        raise MalformedManifestError(message)
    return (
        value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    )


def _to_json(manifest: RawManifest) -> dict[str, object]:
    """The manifest as plain JSON values, before serialisation."""
    origin = manifest.provenance
    return {
        "format": MANIFEST_FORMAT,
        "source_id": origin.source_id,
        "original_identifier": origin.original_identifier,
        "source_version": origin.source_version,
        "payload_kind": origin.payload_kind,
        "retrieved_at": instant(origin.retrieved_at),
        "media_type": origin.media_type,
        "valid_from": None if origin.valid_from is None else instant(origin.valid_from),
        "valid_to": None if origin.valid_to is None else instant(origin.valid_to),
        "spatial_extent": (
            None if origin.spatial_extent is None else dict(origin.spatial_extent)
        ),
        "sha256": manifest.sha256.hex(),
        "byte_count": manifest.byte_count,
    }


_FIELDS = frozenset(
    (
        "format",
        "source_id",
        "original_identifier",
        "source_version",
        "payload_kind",
        "retrieved_at",
        "media_type",
        "valid_from",
        "valid_to",
        "spatial_extent",
        "sha256",
        "byte_count",
    )
)
"""Every key :func:`_to_json` writes. Anything else in a file is refused."""


def _from_json(stored: Mapping[str, object]) -> RawManifest:
    """Rebuild a manifest from an already format-checked mapping.

    ``Provenance`` runs its own completeness check on construction, so a file
    missing an origin field fails the same way a bad call would — one rule, one
    place, whichever direction the value arrived from.
    """
    try:
        origin = Provenance(
            source_id=_text(stored, "source_id"),
            original_identifier=_text(stored, "original_identifier"),
            source_version=_text(stored, "source_version"),
            payload_kind=_text(stored, "payload_kind"),
            retrieved_at=_moment(stored, "retrieved_at"),
            media_type=_text(stored, "media_type"),
            valid_from=_optional_moment(stored, "valid_from"),
            valid_to=_optional_moment(stored, "valid_to"),
            spatial_extent=_extent(stored),
        )
    except ValueError as exc:
        message = f"manifest is not a complete provenance: {exc}"
        raise MalformedManifestError(message) from exc
    return RawManifest(
        provenance=origin,
        sha256=_digest(stored),
        byte_count=_whole(stored, "byte_count"),
    )


def _mapping(value: object, name: str) -> Mapping[str, object]:
    """``value`` as a JSON object, or a refusal naming what it was."""
    if not isinstance(value, Mapping):
        message = f"{name} is {type(value).__name__}, not an object"
        raise MalformedManifestError(message)
    return {str(key): item for key, item in value.items()}


def _text(stored: Mapping[str, object], name: str) -> str:
    value = stored.get(name)
    if not isinstance(value, str):
        message = f"{name} is {type(value).__name__}, not a string"
        raise MalformedManifestError(message)
    return value


def _whole(stored: Mapping[str, object], name: str) -> int:
    value = stored.get(name)
    if not isinstance(value, int) or isinstance(value, bool):
        message = f"{name} is {type(value).__name__}, not a whole number"
        raise MalformedManifestError(message)
    return value


def _digest(stored: Mapping[str, object]) -> bytes:
    """``sha256`` as raw bytes, refusing anything that is not 64 hex digits."""
    text = _text(stored, "sha256")
    if len(text) != _DIGEST_HEX:
        message = f"sha256 is {len(text)} hex digits, not {_DIGEST_HEX}"
        raise MalformedManifestError(message)
    try:
        return bytes.fromhex(text)
    except ValueError as exc:
        message = f"sha256 is not hexadecimal: {text!r}"
        raise MalformedManifestError(message) from exc


def _moment(stored: Mapping[str, object], name: str) -> datetime:
    """One instant, parsed back from what :func:`instant` wrote."""
    text = _text(stored, name)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        message = f"{name} is not an ISO 8601 instant: {text!r}"
        raise MalformedManifestError(message) from exc
    if parsed.tzinfo is None:
        message = f"{name} carries no offset: {text!r}"
        raise MalformedManifestError(message)
    return parsed.astimezone(UTC)


def _optional_moment(stored: Mapping[str, object], name: str) -> datetime | None:
    if stored.get(name) is None:
        return None
    return _moment(stored, name)


def _extent(stored: Mapping[str, object]) -> dict[str, float] | None:
    """The bounding box, as a mapping of names to finite numbers."""
    value = stored.get("spatial_extent")
    if value is None:
        return None
    box = _mapping(value, "spatial_extent")
    edges = {}
    for edge, number in box.items():
        if isinstance(number, bool) or not isinstance(number, (int, float)):
            message = f"spatial_extent.{edge} is {type(number).__name__}, not a number"
            raise MalformedManifestError(message)
        edges[edge] = float(number)
    return edges
