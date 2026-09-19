"""Which capture the station stops, starts and stops holding on each tick.

The station loop's execution half. The loop owns the heartbeat, the queue and the
transport; this owns the one fact the loop cannot rebuild from disk — what has
been begun and ended in this process — and turns the executor's capture windows
into calls on it. Split from :mod:`meridian_client.station_loop` when D-121 gave
those decisions enough rules to be worth testing on their own.

**The order of the three calls matters**, and the loop makes them in this order:
stop, start, drain the executor into the queue, forget. Stopping before starting
lets a pass's clipped tail hand straight over to the next pass's head on one
tick; forgetting after the drain means a result is on disk before its assignment
leaves ``held_assignments``.

Reference: docs/MSP-SPEC.md §4.2, §4.4; docs/DECISIONS.md D-003, D-067, D-121.
"""

from __future__ import annotations

from datetime import datetime

from meridian_client.assignment_message import Assignment
from meridian_client.capture_schedule import clip_to_next, next_edge, select_capture
from meridian_client.execution import CaptureWindow, ExecutionStatus, PassExecutor
from meridian_client.held_assignments import AssignmentRecord

__all__ = ["CaptureSequence"]


class CaptureSequence:
    """Begins and ends one executor's work against one record of held work.

    Args:
        record: What the station holds. Read on every call and trimmed by
            :meth:`forget_handed_over`; delivery into it stays the loop's.
        executor: What receives, and the authority on its own capture windows
            and on what it has not yet finished.
    """

    def __init__(self, record: AssignmentRecord, executor: PassExecutor) -> None:
        """Start with nothing begun. A restart resumes from the record alone."""
        self._record = record
        self._executor = executor
        self._executing: Assignment | None = None
        self._ended: set[str] = set()

    def executing(self) -> Assignment | None:
        """What has been begun and not yet ended, or ``None``."""
        return self._executing

    def status(self) -> ExecutionStatus:
        """The executor's account of itself, for this tick's heartbeat."""
        return self._executor.status(self._executing)

    def next_edge(self, now: datetime) -> datetime | None:
        """The next instant a capture opens or closes, for the loop to wake at."""
        return next_edge(self._windows(), now)

    def stop_finished(self, now: datetime) -> tuple[str | None, tuple[str, ...]]:
        """End the capture whose window closed, and any held work never begun.

        Args:
            now: Timezone-aware UTC.

        Returns:
            The id of the capture ended, if one was, and the ids of held
            assignments whose capture windows closed without being begun.

        Note:
            **Work never begun is still ended.** The station took it — it named
            it in every heartbeat — so dropping it silently would reach the
            platform as a decline (D-003), which is not what happened. It is
            handed to the executor instead, whose honest report is
            ``not_attempted`` (D-121). Each assignment is ended once in a
            process's life; after a restart, the executor's own account of what
            it is still finishing is what prevents a second ``end``.
        """
        windows = self._windows()
        ended = None
        running = self._executing
        if running is not None:
            window = windows.get(running.assignment_id)
            if window is None:
                window = self._executor.capture_window(running)
            if window.closes_at < now:
                self._end(running)
                self._executing = None
                ended = running.assignment_id

        unfinished = self._executor.status(self._executing).unfinished
        not_begun = [
            one
            for one in self._record.held()
            if windows[one.assignment_id].closes_at < now
            and one.assignment_id not in self._ended
            and one.assignment_id not in unfinished
        ]
        for one in not_begun:
            self._end(one)
        return ended, tuple(one.assignment_id for one in not_begun)

    def start_due(self, now: datetime) -> str | None:
        """Begin the held assignment whose capture is open, if none is running.

        Args:
            now: Timezone-aware UTC.

        Returns:
            The id begun, or ``None``.
        """
        if self._executing is not None:
            return None
        candidate = select_capture(
            self._record.held(),
            self._windows(),
            now,
            exclude={*self._ended, *self._executor.status(None).unfinished},
        )
        if candidate is None:
            return None
        self._executor.begin(candidate)
        self._executing = candidate
        return candidate.assignment_id

    def forget_handed_over(self, now: datetime) -> None:
        """Stop holding work whose capture has closed and whose result is queued.

        Args:
            now: Timezone-aware UTC.

        Raises:
            OSError: The record could not be rewritten, as from
                :meth:`AssignmentRecord.drop_closed`.

        Note:
            Called after the loop has drained the executor, so the result of a
            pass ended on this tick is already in the queue. Work the executor
            has not finished — a widened tail, a running decoder — goes on being
            named, which keeps the platform from expiring it (D-067, D-121).
        """
        keep = {
            *(
                name
                for name, window in self._windows().items()
                if window.closes_at >= now
            ),
            *self._executor.status(self._executing).unfinished,
        }
        if self._executing is not None:
            keep.add(self._executing.assignment_id)
        self._record.drop_closed(now, keep=keep)
        self._ended.intersection_update(self._record.held_ids())

    def _windows(self) -> dict[str, CaptureWindow]:
        """Every held assignment's capture window, as the executor asks for it."""
        return clip_to_next(self._record.held(), self._executor.capture_window)

    def _end(self, assignment: Assignment) -> None:
        """Call ``end`` once, and remember that it was called."""
        self._executor.end(assignment)
        self._ended.add(assignment.assignment_id)
