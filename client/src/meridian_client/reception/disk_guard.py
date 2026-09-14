"""Refusing a capture the disk could not hold (D-123).

A full disk mid-capture is a worse outcome than a pass never started: the
recording is truncated, the decode fails, and whatever else shares the disk — the
held record, the upload queue — fails with it. So before each capture the
estimated recording, times a margin, plus a fixed reserve, must be free; if not,
the pass is ``not_attempted`` with the reason in ``client_notes``.

Reference: docs/DECISIONS.md D-060, D-123.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from meridian_client.reception.protocols import CapturePlan, CaptureRefusedError

__all__ = ["DiskGuard"]


def _free_bytes(path: Path) -> int:
    """Free space on the filesystem holding ``path``, or its nearest existing parent."""
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return shutil.disk_usage(probe).free


@dataclass(frozen=True, slots=True)
class DiskGuard:
    """How much space a capture needs, and where to find out how much there is."""

    bytes_per_second: int = 2_048_000
    """The receiver's write rate. The default is ``rtl_sdr`` at 1.024 Msps in
    ``u8``: two bytes a sample."""

    margin: float = 1.25
    """Headroom over the estimate, for a capture window stretched by clipping."""

    reserve_bytes: int = 1024**3
    """Kept free whatever the estimate, for the record, the queue and the logs."""

    free_bytes: Callable[[Path], int] = field(default=_free_bytes)

    def __post_init__(self) -> None:
        """Refuse a guard that could never, or always, pass."""
        if self.bytes_per_second < 0 or self.reserve_bytes < 0 or self.margin < 1.0:
            raise ValueError(
                "a disk guard needs non-negative sizes and a margin of 1 or more"
            )

    def require_room_for(self, plan: CapturePlan) -> None:
        """Raise unless ``plan``'s recording fits with the margin and reserve.

        Raises:
            CaptureRefusedError: With the space needed and the space free.
        """
        seconds = max(0.0, (plan.closes_at - plan.opens_at).total_seconds())
        needed = int(seconds * self.bytes_per_second * self.margin) + self.reserve_bytes
        free = self.free_bytes(plan.folder)
        if free < needed:
            raise CaptureRefusedError(
                f"not enough disk for the capture: {needed} bytes needed, {free} free"
            )
