"""The one byte rendering every snapshot file and manifest is hashed over.

A snapshot is named by its hash, and Stage 15's gate is that the same inputs
always give the same hash. So the bytes of a row must depend on its values and
on nothing else: not the order a query returned its columns, not the zone a
timestamp was read in, not the platform's float formatting of the day.

D-070's rules, generalised from one observation to any row (D-144):

* **keys sorted, no whitespace**, and text written as UTF-8, not escaped;
* **timestamps in UTC, truncated to milliseconds, with a ``Z``** — the
  resolution every stored digest in this project already uses;
* **arrays keep their order**, because an array here is a series or a list the
  database stored in order, and sorting it would make two different
  measurements hash the same;
* **``bytes`` are lowercase hex**, since digests are stored as ``bytea``;
* **``NaN`` and infinity are refused.** JSON has no literal for them, and a
  hash over a value that cannot be written faithfully is a hash of something
  that was never stored.

**Anything else is refused rather than guessed.** ``json.dumps`` would turn an
integer key into a string and a ``Decimal`` into an error only sometimes; a
snapshot that hashed a value one way on one machine and another way on the next
is the failure this module exists to prevent, so the caller converts first.

Deliberately separate from :mod:`meridian.observations.canonical_body`. That
module's bytes are pinned by every ``content_sha256`` already stored (D-118),
so it cannot be generalised in place without risking all of them.

Reference: docs/DECISIONS.md D-070, D-144.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from datetime import UTC, datetime
from functools import singledispatch

__all__ = ["canonical_bytes", "canonical_line", "canonical_value"]


def canonical_bytes(value: object) -> bytes:
    """Render one value as canonical JSON.

    Args:
        value: A row, a manifest, or anything :func:`canonical_value` accepts.

    Returns:
        UTF-8 JSON with sorted keys and no whitespace.

    Raises:
        TypeError: The value holds a type with no single canonical rendering.
        ValueError: The value holds a naive datetime or a non-finite float.
    """
    return json.dumps(
        canonical_value(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def canonical_line(row: object) -> bytes:
    """One row of a JSON Lines file: :func:`canonical_bytes` and a newline.

    Args:
        row: The row, a mapping keyed by column name. Typed ``object`` because
            a list would render as valid JSON and quietly be a line that is not
            a row, so it is checked here rather than trusted.

    Returns:
        The row's canonical bytes, terminated by one newline.

    Raises:
        TypeError: ``row`` is not a mapping, or holds an unrenderable value.
        ValueError: As :func:`canonical_bytes`.
    """
    if not isinstance(row, Mapping):
        message = f"a snapshot row is a mapping, not {type(row).__name__}"
        raise TypeError(message)
    return canonical_bytes(row) + b"\n"


@singledispatch
def canonical_value(value: object) -> object:
    """The value as plain JSON types, ready for :func:`json.dumps`.

    Args:
        value: ``None``, ``bool``, ``int``, ``float``, ``str``, an aware
            ``datetime``, ``bytes``, a mapping with string keys, or a list or
            tuple of any of these.

    Returns:
        The same value built only from JSON's own types.

    Raises:
        TypeError: A type outside that list, or a mapping key that is not a
            string.
        ValueError: A naive datetime, or a float that is NaN or infinite.

    Note:
        Dispatched on type, so what is accepted is read in the registrations
        below rather than in a chain of branches. This base case is everything
        not registered, and refuses it.
    """
    message = f"{type(value).__name__} has no canonical rendering; convert it first"
    raise TypeError(message)


@canonical_value.register(type(None))
@canonical_value.register(bool)
@canonical_value.register(int)
@canonical_value.register(str)
def _as_is(value: object) -> object:
    """JSON's own scalars, unchanged. ``bool`` is kept apart from ``1`` by json."""
    return value


@canonical_value.register(list)
@canonical_value.register(tuple)
def _array(value: list[object] | tuple[object, ...]) -> list[object]:
    """A list or tuple as one JSON array, in its own order."""
    return [canonical_value(one) for one in value]


@canonical_value.register(bytes)
def _hex(value: bytes) -> str:
    """A digest as it is stored in ``bytea``, written as lowercase hex."""
    return value.hex()


@canonical_value.register(Mapping)
def _mapping(value: Mapping[object, object]) -> dict[str, object]:
    """A mapping with its values rendered, refusing any key that is not text.

    ``json.dumps`` would render ``{1: "a"}`` as ``{"1": "a"}``, which collides
    with a mapping that really was keyed by the string — two rows, one hash.
    """
    rendered: dict[str, object] = {}
    for key, one in value.items():
        if not isinstance(key, str):
            message = f"mapping keys must be strings, not {type(key).__name__}"
            raise TypeError(message)
        rendered[key] = canonical_value(one)
    return rendered


@canonical_value.register(float)
def _finite(value: float) -> float:
    """The float unchanged, or refused if JSON cannot write it."""
    if not math.isfinite(value):
        message = f"{value!r} cannot be written as JSON, so it cannot be hashed"
        raise ValueError(message)
    return value


@canonical_value.register(datetime)
def _instant(instant: datetime) -> str:
    """UTC, millisecond precision, ``Z`` suffix — D-070's timestamp.

    Raises:
        ValueError: The instant is naive, which would be hashed as though it
            were UTC whatever zone it was actually read in.
    """
    if instant.tzinfo is None:
        message = "snapshot timestamps must be timezone-aware"
        raise ValueError(message)
    return (
        instant.astimezone(UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
