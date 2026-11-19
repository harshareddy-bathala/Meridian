"""What a search of one area returns, and what it searches with.

The records :mod:`.search` fills and the command prints: the passes that were
read, the one chosen, and the raw-store paths of what was kept. Split from the
search so the search reads as the rule it applies, and so a later part — the
renderer, the publisher — can name these records without importing the code
that reads a network.

Reference: docs/DECISIONS.md D-372.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from meridian_ingest.imagery.areas import PublishedArea
from meridian_ingest.imagery.cloud import CloudCount
from meridian_ingest.imagery.pixels import PixelReader, ReadWindow
from meridian_ingest.imagery.sources import ImagerySource
from meridian_ingest.imagery.stac import SceneCandidate
from meridian_ingest.politeness import RequestBudget
from meridian_ingest.raw_store import RawStore
from meridian_ingest.retrieval import Retriever

__all__ = ["Considered", "Granule", "SceneSearch", "SearchContext"]


@dataclass(slots=True)
class SearchContext:
    """What one run searches with: the network seams, the store, and the clock.

    ``now`` is the instant the run asks about, fixed for the run, so every
    area's lookback ends at the same moment. ``clock`` stamps each artefact as
    it arrives.
    """

    retriever: Retriever
    pixels: PixelReader
    store: RawStore
    budget: RequestBudget
    now: datetime
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(tz=UTC))


@dataclass(frozen=True, slots=True)
class Granule:
    """One asset's window, read from one granule of a pass."""

    candidate: SceneCandidate
    asset: str
    window: ReadWindow


@dataclass(frozen=True, slots=True)
class Considered:
    """One pass that was read, and what its classification layer said."""

    key: str
    acquired_at: datetime
    item_ids: tuple[str, ...]
    scene_cloud: float | None
    cloud: CloudCount | None
    refused: str | None
    """Why it cannot be chosen, or None when it can."""

    raw_paths: tuple[str, ...]
    """The classification windows it was counted from."""

    @property
    def cloud_fraction(self) -> float | None:
        """The share of the box behind cloud, or None if it was never counted."""
        return None if self.cloud is None else self.cloud.fraction


@dataclass(frozen=True, slots=True)
class SceneSearch:
    """One area's search of one source: what was considered and what was kept."""

    area: PublishedArea
    source: ImagerySource
    search_path: str | None
    considered: tuple[Considered, ...]
    chosen: Considered | None
    windows: tuple[str, ...]
    """The display windows (or tiles) of the chosen scene, in the raw store."""

    notes: tuple[str, ...]

    @property
    def skipped(self) -> str | None:
        """Why no scene was chosen, or None when one was."""
        if self.chosen is not None:
            return None
        if not self.considered:
            return "no scene in the lookback reaches the box under the cloud ceiling"
        return "no pass read covers the box with valid pixels"
