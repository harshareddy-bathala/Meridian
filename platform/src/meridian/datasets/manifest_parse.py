"""Strict readers for a parsed ``manifest.json``: one type, or an error by name.

Split from :mod:`meridian.datasets.manifest` so that module reads as what a
manifest *is*, and this one as how a file is refused. Every reader names the
field it was reading, because "expected an integer" is not an error anyone can
act on in a manifest with four integers in it.

Nothing here coerces. A row count written as ``"12"`` is refused rather than
read as twelve: a manifest is written by :func:`manifest_bytes` and nothing
else, so a value of the wrong type means the file is not the one we wrote.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

__all__ = [
    "MalformedManifestError",
    "digest",
    "instant",
    "mapping",
    "optional_digest",
    "optional_text",
    "rows_of",
    "text",
    "whole",
]

_HEX_DIGEST = 64


class MalformedManifestError(ValueError):
    """A manifest that cannot be read back, or could not describe a directory."""


def mapping(value: object, what: str) -> Mapping[str, object]:
    """A JSON object, with string keys."""
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        message = f"{what} is not an object"
        raise MalformedManifestError(message)
    return value


def text(value: object, what: str) -> str:
    """A non-empty string."""
    if not isinstance(value, str) or not value.strip():
        message = f"{what} is not a non-empty string"
        raise MalformedManifestError(message)
    return value


def optional_text(value: object, what: str) -> str | None:
    """:func:`text`, or ``None``."""
    return None if value is None else text(value, what)


def whole(value: object, what: str) -> int:
    """A non-negative integer — and not ``True``, which JSON would allow."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        message = f"{what} is not a non-negative integer"
        raise MalformedManifestError(message)
    return value


def digest(value: object, what: str) -> bytes:
    """A sha256 written as 64 lowercase hex digits."""
    if (
        not isinstance(value, str)
        or len(value) != _HEX_DIGEST
        or value != value.lower()
    ):
        message = f"{what} is not a sha256 in lowercase hex"
        raise MalformedManifestError(message)
    try:
        return bytes.fromhex(value)
    except ValueError as exc:
        message = f"{what} is not a sha256 in lowercase hex"
        raise MalformedManifestError(message) from exc


def optional_digest(value: object, what: str) -> bytes | None:
    """:func:`digest`, or ``None``."""
    return None if value is None else digest(value, what)


def instant(value: object, what: str) -> datetime:
    """A UTC timestamp as the canonical rendering writes one: ``...Z``."""
    if not isinstance(value, str) or not value.endswith("Z"):
        message = f"{what} is not a UTC timestamp"
        raise MalformedManifestError(message)
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        message = f"{what} is not a UTC timestamp"
        raise MalformedManifestError(message) from exc


def rows_of(
    value: object, what: str, fields: tuple[str, ...]
) -> list[Mapping[str, object]]:
    """A list of objects, each with exactly ``fields``."""
    if not isinstance(value, list):
        message = f"{what} is not a list"
        raise MalformedManifestError(message)
    rows = [mapping(one, what) for one in value]
    for one in rows:
        if set(one) != set(fields):
            message = f"an entry in {what} has fields {sorted(one)}, not {list(fields)}"
            raise MalformedManifestError(message)
    return rows
