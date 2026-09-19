"""Choosing a capture, clipping its tail and finding the next edge.

Pure functions over plain values, so every rule about widened, clipped and
overlapping windows is checked here without a loop, a disk or a clock.

Marked as a unit test by living in ``tests/unit``.

Reference: docs/MSP-SPEC.md §4.3; docs/DECISIONS.md D-021, D-065, D-121.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from meridian_client.assignment_message import Assignment, ElementSet
from meridian_client.capture_schedule import clip_to_next, next_edge, select_capture
from meridian_client.execution import CaptureWindow, assignment_capture_window

NOW = datetime(2026, 8, 14, 9, 31, 2, tzinfo=UTC)

ELEMENT_SET = ElementSet(
    epoch=datetime(2026, 8, 14, 2, 11, 0, tzinfo=UTC),
    line1="1 25544U 98067A   26226.50000000  .00001234  00000-0  12345-4 0  9991",
    line2="2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.50377579123456",
)


def assignment(
    assignment_id: str, *, starts_in_minutes: float = 10, minutes_long: float = 11
) -> Assignment:
    """One Meteor-M LRPT pass, with only its id and window varied."""
    start_at = NOW + timedelta(minutes=starts_in_minutes)
    return Assignment(
        assignment_id=assignment_id,
        satellite_id="norad:57166",
        start_at=start_at,
        end_at=start_at + timedelta(minutes=minutes_long),
        centre_freq_hz=137_900_000,
        mode="lrpt",
        expected_max_elevation_deg=61.4,
        predicted_yield=None,
        element_set=ELEMENT_SET,
        timing_uncertainty_s=4.2,
        priority=1.0,
    )


def widened_by(minutes: float) -> Callable[[Assignment], CaptureWindow]:
    """An executor's answer that adds ``minutes`` of margin at each end."""
    margin = timedelta(minutes=minutes)

    def window_for(one: Assignment) -> CaptureWindow:
        return CaptureWindow(one.start_at - margin, one.end_at + margin)

    return window_for


def at(minutes: float) -> datetime:
    return NOW + timedelta(minutes=minutes)


# --- select_capture -----------------------------------------------------------


def test_two_open_windows_resolve_to_the_one_that_started_first() -> None:
    """The scheduler does not issue overlapping windows to one station (D-065), so
    this is the case where something else went wrong — two configurations
    scheduled, or a clock stepped. Finishing the pass already under way beats
    abandoning it for one that just began, and either way the choice has to be
    the same on every tick or the station would thrash between them.
    """
    started_earlier = assignment("as_first", starts_in_minutes=-5)
    started_later = assignment("as_second", starts_in_minutes=-1)
    held = (started_later, started_earlier)

    chosen = select_capture(held, clip_to_next(held, assignment_capture_window), NOW)

    assert chosen is not None
    assert chosen.assignment_id == "as_first"


def test_nothing_is_due_before_a_capture_opens() -> None:
    """With the assignment's own window, the start the platform gave is the start:
    it already widened the window by its own timing uncertainty (D-021)."""
    held = (assignment("as_a", starts_in_minutes=5),)

    assert (
        select_capture(held, clip_to_next(held, assignment_capture_window), NOW) is None
    )


def test_nothing_is_due_after_a_capture_closes() -> None:
    """Symmetric, and the reason the loop can ask on every tick unguarded."""
    held = (assignment("as_a", starts_in_minutes=-30),)

    assert (
        select_capture(held, clip_to_next(held, assignment_capture_window), NOW) is None
    )


def test_a_widened_capture_is_due_before_the_assignment_opens() -> None:
    """MSP §4.3's widening, asked for by the executor rather than assumed (D-121)."""
    held = (assignment("as_a", starts_in_minutes=1),)

    chosen = select_capture(held, clip_to_next(held, widened_by(2)), NOW)

    assert chosen is not None
    assert chosen.assignment_id == "as_a"


def test_work_the_executor_is_still_finishing_is_not_begun_again() -> None:
    held = (assignment("as_a", starts_in_minutes=-1),)
    windows = clip_to_next(held, assignment_capture_window)

    assert select_capture(held, windows, NOW, exclude={"as_a"}) is None


# --- clip_to_next -------------------------------------------------------------


def test_the_last_capture_keeps_its_whole_widened_window() -> None:
    held = (assignment("as_a", starts_in_minutes=10),)

    windows = clip_to_next(held, widened_by(2))

    assert windows["as_a"] == CaptureWindow(at(8), at(23))


def test_a_widened_tail_is_cut_where_the_next_capture_opens() -> None:
    """One pass's tail never delays the next pass's head."""
    first = assignment("as_first", starts_in_minutes=0, minutes_long=10)
    second = assignment("as_second", starts_in_minutes=13, minutes_long=10)

    windows = clip_to_next((second, first), widened_by(2))

    assert windows["as_first"] == CaptureWindow(at(-2), at(11))
    assert windows["as_second"] == CaptureWindow(at(11), at(25))


def test_a_clipped_tail_never_cuts_into_the_pass_itself() -> None:
    """Only the margin yields: the next capture may want to open inside this pass,
    and this pass's ``end_at`` is kept anyway."""
    first = assignment("as_first", starts_in_minutes=0, minutes_long=10)
    second = assignment("as_second", starts_in_minutes=11, minutes_long=10)

    windows = clip_to_next((first, second), widened_by(2))

    assert windows["as_first"].closes_at == first.end_at


def test_a_tail_is_cut_by_the_earliest_later_opening_not_only_the_next() -> None:
    """A later pass with a wider margin can open before the one after this."""
    first = assignment("as_first", starts_in_minutes=0, minutes_long=10)
    second = assignment("as_second", starts_in_minutes=14, minutes_long=5)
    third = assignment("as_third", starts_in_minutes=15, minutes_long=5)

    def window_for(one: Assignment) -> CaptureWindow:
        margin = timedelta(minutes=4 if one is third else 2)
        return CaptureWindow(one.start_at - margin, one.end_at + margin)

    windows = clip_to_next((first, second, third), window_for)

    assert windows["as_first"].closes_at == at(11)


def test_an_executor_cannot_capture_less_than_the_pass() -> None:
    """A capture closing before ``end_at`` would drop work still in its window."""
    held = (assignment("as_a", starts_in_minutes=0, minutes_long=10),)

    def narrower(one: Assignment) -> CaptureWindow:
        minute = timedelta(minutes=1)
        return CaptureWindow(one.start_at + minute, one.end_at - minute)

    windows = clip_to_next(held, narrower)

    assert windows["as_a"] == CaptureWindow(at(0), at(10))


# --- next_edge ----------------------------------------------------------------


def test_the_next_edge_is_the_soonest_opening_or_closing_still_to_come() -> None:
    windows = {
        "as_running": CaptureWindow(at(-5), at(6)),
        "as_later": CaptureWindow(at(4), at(15)),
    }

    assert next_edge(windows, NOW) == at(4)


def test_an_edge_at_now_has_already_happened() -> None:
    """Strictly after, or a loop woken at an edge would sleep for it again."""
    windows = {"as_a": CaptureWindow(NOW, at(10))}

    assert next_edge(windows, NOW) == at(10)


def test_there_is_no_next_edge_when_every_capture_has_closed() -> None:
    windows = {"as_a": CaptureWindow(at(-20), at(-9))}

    assert next_edge(windows, NOW) is None
    assert next_edge({}, NOW) is None
