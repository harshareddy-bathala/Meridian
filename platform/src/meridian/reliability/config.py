"""The parameters a classification is decided under, and their hash.

Every stored classification records the sha256 of these parameters, so a
reliability figure names exactly what it was counted under, and changing a
parameter writes new rows beside the old rather than restating them (D-182).

The defaults are the snapshot labeller's (``LabelConfig``, D-146, D-147), so a
pass classified live and the same pass labelled from a snapshot are judged
under the same margins unless someone chooses otherwise.

Reference: docs/DECISIONS.md D-146, D-147, D-182.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

__all__ = ["ClassificationConfig"]


@dataclass(frozen=True, slots=True)
class ClassificationConfig:
    """The margins the live accounting classifies under."""

    settle_margin_s: int = 86_400
    """How long after a window closes its pass waits to be classified. A
    station queues reports through an outage, and without a margin a report
    still on its way would be read as absence (D-146)."""

    silent_window_s: int = 43_200
    """How far either side of a pass another reception still counts as
    evidence about the satellite (D-147)."""

    silent_min_attempts: int = 2
    """Confirmed silences needed to call a satellite silent (D-147)."""

    def __post_init__(self) -> None:
        """Refuse a margin or a count that cannot mean anything."""
        for name, value in asdict(self).items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer, not {value!r}")

    def parameters(self) -> dict[str, int]:
        """The parameters as recorded with every classification."""
        return asdict(self)

    def sha256(self) -> bytes:
        """The hash stored beside every classification made under these."""
        text = json.dumps(self.parameters(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode("utf-8")).digest()
