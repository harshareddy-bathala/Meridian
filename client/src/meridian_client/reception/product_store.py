"""Where a station keeps the products it declares, named by their hashes.

A capture folder is pruned 30 days after its result is handed over (D-123), so
a product left there would disappear while the platform still recorded it as
held. A product the decoder names is therefore copied here, to
``products/<sha256>``, when its decode settles (D-176). The observation declares
it as ``station:products/<sha256>``: held by the station that reported it, and
not an address anyone can fetch, since MSP defines no transfer yet (D-029).

**Content-addressed, so keeping is idempotent.** Two receptions producing one
identical file keep one copy, and a restart that keeps a file again changes
nothing. A copy is written to a temporary name, synced, and renamed into place,
so the store never holds a truncated file under a real hash.

**Capped, oldest first.** A Pi's disk is not an archive. When a new product
takes the store over its cap, the least recently kept products are removed until
it fits. The one just kept is never removed. A product larger than the whole cap
is not kept at all, and so is never declared. An evicted product is not
reported: MSP has no message for it, and the platform's row says where a product
was declared held, not that it is still there (D-176).

Reference: docs/DECISIONS.md D-029, D-123, D-176.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "DEFAULT_MAX_BYTES",
    "HeldProduct",
    "ProductStore",
    "digest_of",
]

_log = logging.getLogger(__name__)

DEFAULT_MAX_BYTES = 2 * 1024**3
"""Two gibibytes: hundreds of waterfalls, and a small part of a Pi's card."""

_CHUNK = 1024 * 1024
_HASH = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class HeldProduct:
    """A product the store holds, as the observation declares it."""

    kind: str
    sha256: str
    """Lowercase hexadecimal."""
    size_bytes: int

    def as_declared(self) -> dict[str, object]:
        """The element of MSP §4.4's ``products`` array."""
        return {
            "kind": self.kind,
            "uri": f"station:products/{self.sha256}",
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


def digest_of(path: Path) -> tuple[str, int]:
    """A file's sha256, in lowercase hexadecimal, and its size in bytes."""
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(_CHUNK):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


class ProductStore:
    """The station's products, one file per hash, under a byte cap.

    Args:
        root: The directory, created on first use.
        max_bytes: The most the store may hold.
    """

    def __init__(self, root: Path, *, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        """Hold the location; nothing is read or written until a product is kept."""
        if max_bytes <= 0:
            raise ValueError(f"max_bytes must be positive, not {max_bytes}")
        self._root = root
        self._max_bytes = max_bytes

    def keep(self, source: Path) -> str | None:
        """Copy ``source`` into the store, and make room for it.

        Returns:
            Its sha256, or ``None`` if it is larger than the whole store or could
            not be written. A product not kept is not declared.
        """
        try:
            sha256, size = digest_of(source)
            if size > self._max_bytes:
                _log.warning(
                    "product %s is %d bytes, over the store's %d; not kept",
                    source,
                    size,
                    self._max_bytes,
                )
                return None
            self._root.mkdir(parents=True, exist_ok=True)
            target = self._root / sha256
            if target.exists():
                os.utime(target)
            else:
                self._write(source, target)
            self._evict(keep=sha256)
        except OSError:
            _log.exception("product %s could not be kept", source)
            return None
        return sha256

    def holds(self, sha256: str) -> bool:
        """Whether the store has this product now."""
        return _HASH.fullmatch(sha256) is not None and (self._root / sha256).is_file()

    def _write(self, source: Path, target: Path) -> None:
        """Copy under a temporary name, sync it, and rename it into place."""
        scratch = self._root / f".{target.name}.partial"
        try:
            with source.open("rb") as reading, scratch.open("wb") as writing:
                shutil.copyfileobj(reading, writing, _CHUNK)
                writing.flush()
                os.fsync(writing.fileno())
            scratch.replace(target)
        finally:
            scratch.unlink(missing_ok=True)
        directory = os.open(self._root, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def _evict(self, *, keep: str) -> None:
        """Remove the least recently kept products until the store fits its cap."""
        held = [
            (one.stat().st_mtime_ns, one.name, one.stat().st_size, one)
            for one in self._root.iterdir()
            if _HASH.fullmatch(one.name) and one.is_file()
        ]
        total = sum(size for _, _, size, _ in held)
        for _, name, size, path in sorted(held):
            if total <= self._max_bytes:
                return
            if name == keep:
                continue
            with contextlib.suppress(FileNotFoundError):
                path.unlink()
            total -= size
            _log.info("product %s evicted to keep the store under its cap", name)
