"""What a manifest may say: its kinds, their lineage, and the files each may hold.

Split from :mod:`meridian.datasets.manifest` when Stage 22 added a fifth kind,
so the rules a reader checks a directory against sit in one short list rather
than in the middle of the parser.

**A file name is ours, and plain.** Every kind holds JSON Lines tables. A model
also holds its one ``model.json`` (D-163). An evaluation report also holds the
report a reader opens, the configuration it was built from, and its figures
(D-235). A name never comes from a row, and a name one kind holds is refused in
another, so a raw snapshot cannot carry a stray report and still verify.

Reference: docs/DECISIONS.md D-144, D-163, D-229, D-235.
"""

from __future__ import annotations

import re
from typing import Literal

from meridian.datasets.manifest_parse import MalformedManifestError

__all__ = [
    "DERIVED",
    "KINDS",
    "WITH_ENVIRONMENT",
    "Kind",
    "check_count",
    "check_digest",
    "check_file_name",
    "check_kind_holds",
    "is_table",
    "unique",
]

Kind = Literal[
    "raw_snapshot",
    "evaluation_dataset",
    "model",
    "regions_report",
    "evaluation_report",
    "fault_run",
]
KINDS: tuple[Kind, ...] = (
    "raw_snapshot",
    "evaluation_dataset",
    "model",
    "regions_report",
    "evaluation_report",
    "fault_run",
)
DERIVED: tuple[Kind, ...] = (
    "evaluation_dataset",
    "model",
    "regions_report",
    "evaluation_report",
)
"""Kinds made from another directory, which name it and how (D-163, D-229)."""

WITH_ENVIRONMENT: tuple[Kind, ...] = ("evaluation_report",)
"""Kinds whose manifest records the machine that made them, unhashed (D-235)."""

_COUNT_NAME = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")
_TABLE = re.compile(r"^[a-z][a-z0-9_]*\.jsonl$")
_FIGURE = re.compile(r"^[a-z][a-z0-9_]*\.svg$")
_NAMED: dict[str, tuple[Kind, ...]] = {
    "model.json": ("model",),
    "report.md": ("evaluation_report",),
    "config.toml": ("evaluation_report",),
}
"""The single files a kind holds besides its tables, and which kinds hold each."""

_SHA256_BYTES = 32


def is_table(name: str) -> bool:
    """Whether ``name`` is a JSON Lines table, whose last row must be whole."""
    return bool(_TABLE.match(name))


def check_file_name(name: str) -> None:
    """Refuse a name no kind could hold."""
    if not (is_table(name) or _FIGURE.match(name) or name in _NAMED):
        message = f"{name!r} is not a snapshot file name"
        raise MalformedManifestError(message)


def check_kind_holds(kind: Kind, name: str) -> None:
    """Refuse a file that belongs to another kind of directory."""
    if is_table(name):
        return
    holders = ("evaluation_report",) if _FIGURE.match(name) else _NAMED.get(name, ())
    if kind not in holders:
        message = f"a {kind.replace('_', ' ')} does not hold {name}"
        raise MalformedManifestError(message)


def check_count(name: str, count: int) -> None:
    """Refuse a count under a name that is not dotted lowercase, or below zero."""
    if not _COUNT_NAME.match(name) or count < 0:
        message = f"count {name!r} = {count} is not a count"
        raise MalformedManifestError(message)


def check_digest(what: str, value: bytes | None) -> None:
    """Refuse anything that is not a sha256."""
    if value is None or len(value) != _SHA256_BYTES:
        size = "nothing" if value is None else f"{len(value)} bytes"
        message = f"{what} is {size}, not a sha256"
        raise MalformedManifestError(message)


def unique(what: str, keys: list[str]) -> None:
    """Refuse two entries under one name; the second would hide the first."""
    seen = {one for one in keys if keys.count(one) > 1}
    if seen:
        message = f"{what} listed more than once: {sorted(seen)}"
        raise MalformedManifestError(message)
