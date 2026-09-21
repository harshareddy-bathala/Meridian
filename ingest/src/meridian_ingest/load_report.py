"""What a load did, in the shape the command line prints.

Separate from :mod:`meridian_ingest.load` so that "what happened" and "how it
happened" stay readable apart, and because this is the part an operator's eyes
land on. Every count here is reported rather than acted upon: nothing in the
loader branches on them.

**Written and already-held are counted apart, never summed.** "Nothing new" and
"nothing there" are different runs, and only the first is the completion gate
passing.

Reference: docs/DECISIONS.md D-133, D-141.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["ArtefactLoad", "LoadReport"]


@dataclass(frozen=True, slots=True)
class ArtefactLoad:
    """What loading one artefact did."""

    raw_path: str
    record_id: int
    record_written: bool
    """False when this artefact was already recorded — an ordinary re-run."""

    superseded: tuple[int, ...] = ()
    """Records this one replaced: same identifier, different bytes.

    Nothing is deleted and no bytes are touched. Both artefacts stay in the raw
    store, because a figure computed from the older one last month must still
    be explainable this month.
    """

    skipped: str | None = None
    """Why nothing was normalised, or None when something was."""

    stations_written: int = 0
    stations_already_held: int = 0
    receptions_written: int = 0
    receptions_already_held: int = 0


@dataclass(frozen=True, slots=True)
class LoadReport:
    """What one source's load did, artefact by artefact."""

    source_id: str
    source_registered: bool
    """True when this run was the one that wrote the source's row."""

    artefacts: tuple[ArtefactLoad, ...] = field(default_factory=tuple)

    @property
    def records_written(self) -> int:
        """Artefacts recorded for the first time by this run."""
        return sum(1 for one in self.artefacts if one.record_written)

    @property
    def skipped(self) -> tuple[ArtefactLoad, ...]:
        """Artefacts nothing was derived from, and why."""
        return tuple(one for one in self.artefacts if one.skipped is not None)

    @property
    def stations_written(self) -> int:
        """Station descriptions this run stored for the first time."""
        return sum(one.stations_written for one in self.artefacts)

    @property
    def receptions_written(self) -> int:
        """Receptions this run stored for the first time."""
        return sum(one.receptions_written for one in self.artefacts)

    @property
    def receptions_already_held(self) -> int:
        """Receptions that were already stored, identically.

        Printed beside the written count rather than folded into it: "nothing
        new" and "nothing there" are different runs, and only one of them is
        the completion gate passing.
        """
        return sum(one.receptions_already_held for one in self.artefacts)

    def wrote_nothing(self) -> bool:
        """Whether this run added no rows at all.

        The completion gate's assertion in one call: load a tree twice, and the
        second run answers True.
        """
        return (
            self.records_written == 0
            and self.stations_written == 0
            and self.receptions_written == 0
        )
