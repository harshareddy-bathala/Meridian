"""The index of published scenes, which the API reads checked whole.

Reference: docs/DECISIONS.md D-369, D-371, D-375, D-378.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from meridian_ingest.imagery import model_checks as check
from meridian_ingest.imagery.model_checks import (
    INDEX_SCHEMA,
    SOURCE_CLASSES,
    SourceClass,
)


@dataclass(frozen=True)
class IndexEntry:
    """One published scene, as the listing names it."""

    hash: str
    area_id: int
    source_class: SourceClass
    source_id: str
    acquired_at: datetime
    published_at: datetime
    size_bytes: int
    simulated: bool = False

    def __post_init__(self) -> None:
        """Refuse a record that breaks one of this module's rules."""
        if not isinstance(self.hash, str) or not check.HASH12.match(self.hash):
            raise check.fail(f"hash {self.hash!r} must be twelve lower-case hex digits")
        check.integer(self.area_id, "area_id", minimum=1)
        if self.source_class not in SOURCE_CLASSES:
            raise check.fail(check.not_a_class(self.source_class))
        check.source_id(self.source_id)
        check.instant(self.acquired_at, "acquired_at")
        check.instant(self.published_at, "published_at")
        check.integer(self.size_bytes, "size_bytes")
        check.boolean(self.simulated, "simulated")

    def to_json(self) -> dict[str, object]:
        """The record as JSON-native values, in the shape it is published."""
        return {
            "hash": self.hash,
            "area_id": self.area_id,
            "source_class": self.source_class,
            "source_id": self.source_id,
            "acquired_at": check.instant(self.acquired_at, "acquired_at"),
            "published_at": check.instant(self.published_at, "published_at"),
            "size_bytes": self.size_bytes,
            "simulated": self.simulated,
        }

    @classmethod
    def from_json(cls, stored: object) -> IndexEntry:
        """Read the record back, refusing a field this version does not know."""
        fields = check.fields(
            stored,
            frozenset(
                {
                    "hash",
                    "area_id",
                    "source_class",
                    "source_id",
                    "acquired_at",
                    "published_at",
                    "size_bytes",
                    "simulated",
                }
            ),
            "index entry",
        )
        source_class = fields["source_class"]
        if source_class not in SOURCE_CLASSES:
            raise check.fail(check.not_a_class(source_class))
        return cls(
            check.text(fields["hash"], "hash"),
            check.integer(fields["area_id"], "area_id", minimum=1),
            source_class,
            check.text(fields["source_id"], "source_id"),
            check.parse_instant(fields["acquired_at"], "acquired_at"),
            check.parse_instant(fields["published_at"], "published_at"),
            check.integer(fields["size_bytes"], "size_bytes"),
            check.boolean(fields["simulated"], "simulated"),
        )


@dataclass(frozen=True)
class ImageryIndex:
    """Every scene published, which the API reads checked whole (D-369's rule).

    ``published_at`` is in the index and not the manifest: when a directory was
    published is a fact about this deployment, not about the scene, and in the
    manifest it would give the same scene a different digest every day.
    """

    scenes: tuple[IndexEntry, ...]
    schema: str = INDEX_SCHEMA

    def __post_init__(self) -> None:
        """Refuse a record that breaks one of this module's rules."""
        if self.schema != INDEX_SCHEMA:
            raise check.fail(f"schema must be {INDEX_SCHEMA}, not {self.schema!r}")
        hashes = [entry.hash for entry in self.scenes]
        if len(set(hashes)) != len(hashes):
            raise check.fail("a scene is listed more than once")

    def to_json(self) -> dict[str, object]:
        """The record as JSON-native values, in the shape it is published."""
        return {
            "schema": self.schema,
            "scenes": [entry.to_json() for entry in self.scenes],
        }

    @classmethod
    def from_json(cls, stored: object) -> ImageryIndex:
        """Read the record back, refusing a field this version does not know."""
        fields = check.fields(stored, frozenset({"schema", "scenes"}), "index")
        return cls(
            scenes=tuple(
                IndexEntry.from_json(item)
                for item in check.sequence(fields["scenes"], "scenes")
            ),
            schema=check.text(fields["schema"], "schema"),
        )
