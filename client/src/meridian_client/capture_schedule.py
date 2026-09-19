"""Which held assignment to be recording, and when that next changes.

The station loop asks its executor how wide it wants each capture (D-121); this
module turns those answers into decisions. It is pure — plain values in, plain
values out, no clock and no disk — so every rule about overlapping, clipped and
widened windows is tested without running a loop.

Reference: docs/MSP-SPEC.md §4.3; docs/DECISIONS.md D-021, D-065, D-121.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from datetime import datetime

from meridian_client.assignment_message import Assignment
from meridian_client.execution import CaptureWindow

__all__ = ["clip_to_next", "next_edge", "select_capture"]


def clip_to_next(
    held: Sequence[Assignment],
    window_for: Callable[[Assignment], CaptureWindow],
) -> dict[str, CaptureWindow]:
    """Every held assignment's capture window, as the loop will act on it.

    Args:
        held: What the station holds.
        window_for: The executor's answer for one assignment.

    Returns:
        Each window keyed on its assignment id, **never narrower than the
        assignment's own window**, and with its widened tail cut where a later
        assignment's capture opens.

    Note:
        **One pass's tail never delays the next pass's head.** The tail beyond
        ``end_at`` is margin; the next pass's opening is data. Only the margin
        yields, though: the scheduler issues one station non-overlapping windows
        (D-065), so ``end_at`` is kept even when a later capture wants to open
        before it. A station has one antenna chain, and abandoning a pass while
        it is still up for the lead-in of the next is the wrong trade.
    """
    ordered = sorted(held, key=lambda one: (one.start_at, one.assignment_id))
    covering = [_covering(one, window_for(one)) for one in ordered]

    clipped: dict[str, CaptureWindow] = {}
    later_opens_at: datetime | None = None
    for one, window in zip(reversed(ordered), reversed(covering), strict=True):
        closes_at = window.closes_at
        if later_opens_at is not None:
            closes_at = min(closes_at, max(later_opens_at, one.end_at))
        clipped[one.assignment_id] = CaptureWindow(window.opens_at, closes_at)
        later_opens_at = (
            window.opens_at
            if later_opens_at is None
            else min(later_opens_at, window.opens_at)
        )
    return clipped


def select_capture(
    held: Sequence[Assignment],
    windows: Mapping[str, CaptureWindow],
    now: datetime,
    *,
    exclude: Collection[str] = (),
) -> Assignment | None:
    """The assignment a station should start recording at ``now``, if any.

    Args:
        held: What the station holds.
        windows: From :func:`clip_to_next`.
        now: Timezone-aware UTC.
        exclude: Ids the executor is still finishing, which must not be begun a
            second time.

    Returns:
        The earliest assignment whose capture window is open, or ``None``.

    Note:
        **Earliest first, and only one.** A station has one antenna chain in
        Phase 1, and the scheduler already guarantees the windows it issues do
        not overlap for one station (D-065). If two are open anyway — because two
        configurations were scheduled, or a clock stepped — taking the earliest
        is the choice that finishes work rather than abandoning a pass already
        under way for one that just began, and it is the same choice on every
        tick, so the station cannot thrash between them.
    """
    open_now = [
        one
        for one in held
        if one.assignment_id not in exclude
        and one.assignment_id in windows
        and windows[one.assignment_id].opens_at
        <= now
        <= windows[one.assignment_id].closes_at
    ]
    if not open_now:
        return None
    return min(open_now, key=lambda one: (one.start_at, one.assignment_id))


def next_edge(windows: Mapping[str, CaptureWindow], now: datetime) -> datetime | None:
    """The first instant after ``now`` at which any capture opens or closes.

    Args:
        windows: From :func:`clip_to_next`.
        now: Timezone-aware UTC.

    Returns:
        That instant, or ``None`` when nothing held has an edge still to come.
        The loop wakes for it, so capture starts on time rather than up to one
        heartbeat late (D-121).
    """
    edges = [
        edge
        for window in windows.values()
        for edge in (window.opens_at, window.closes_at)
        if edge > now
    ]
    return min(edges, default=None)


def _covering(assignment: Assignment, window: CaptureWindow) -> CaptureWindow:
    """``window``, widened where needed to contain the assignment's own.

    An executor may ask for more margin, never for less pass: a capture that
    closed before ``end_at`` would let the loop stop naming work whose window is
    still open, which the platform reads as a decline (D-003).
    """
    return CaptureWindow(
        min(window.opens_at, assignment.start_at),
        max(window.closes_at, assignment.end_at),
    )
