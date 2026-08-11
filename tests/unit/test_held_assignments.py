"""What a station holds, across delivery, redelivery, expiry and a reboot.

Real files in a temporary directory, because every property here is about what
survives — a mocked filesystem would prove nothing about the one failure the
module exists to prevent.

Marked as a unit test by living in ``tests/unit``: no network, no database.

Reference: docs/MSP-SPEC.md §4.2, §4.3; docs/DECISIONS.md D-003, D-067.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from meridian_client.assignment_message import (
    Assignment,
    ElementSet,
    MalformedAssignmentError,
)
from meridian_client.held_assignments import (
    AssignmentRecord,
    due_now,
    merge_by_id,
    still_current,
)

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


def test_redelivery_of_a_held_assignment_adds_nothing() -> None:
    """MSP §4.2 requires a client to deduplicate by `assignment_id`.

    The platform returns the same assignment on every heartbeat until it is
    reported or its window passes. A station that treated each as new would hold
    one pass eight times over and report it eight times in `held_assignments`.
    """
    held = (assignment("as_a"),)

    merged = merge_by_id(held, [assignment("as_a")])

    assert [one.assignment_id for one in merged] == ["as_a"]


def test_a_redelivered_copy_replaces_the_held_one() -> None:
    """The platform's latest statement wins where the two differ.

    A window recomputed from a fresher element set is a better window, and it
    arrives through exactly this path.
    """
    held = (assignment("as_a", starts_in_minutes=10),)

    merged = merge_by_id(held, [assignment("as_a", starts_in_minutes=10.5)])

    assert merged[0].start_at == NOW + timedelta(minutes=10.5)


def test_the_held_set_is_ordered_by_when_the_work_starts() -> None:
    """Soonest first, so a station reading its own record acts in the right order."""
    merged = merge_by_id(
        (),
        [
            assignment("as_late", starts_in_minutes=90),
            assignment("as_soon", starts_in_minutes=5),
        ],
    )

    assert [one.assignment_id for one in merged] == ["as_soon", "as_late"]


def test_an_assignment_is_current_until_its_window_closes() -> None:
    """Dropped on `end_at`, never on `start_at`.

    An assignment whose window has opened is the station's *current* work — the
    same reasoning that put `end_at` on the platform's delivery predicate (D-067,
    D-035).
    """
    under_way = assignment("as_running", starts_in_minutes=-3, minutes_long=11)

    assert still_current((under_way,), NOW) == (under_way,)


def test_an_assignment_whose_window_has_closed_is_dropped() -> None:
    """A pass never repeats, so there is nothing left to do with it."""
    finished = assignment("as_done", starts_in_minutes=-30, minutes_long=11)

    assert still_current((finished,), NOW) == ()


def test_a_new_station_holds_nothing_rather_than_failing(tmp_path: Path) -> None:
    """First boot is the normal path, and `[]` is the correct thing to report."""
    record = AssignmentRecord(tmp_path / "held.json")

    assert record.held() == ()
    assert record.held_ids() == []


def test_what_a_station_accepted_survives_a_restart(tmp_path: Path) -> None:
    """The property the whole module exists for.

    A station that lost its record on reboot would report holding nothing, the
    platform would read that as declining everything (D-003), and the pass would
    be dropped — correctly, but only because the station was honest about it.
    Keeping the record is how it stays covered instead.
    """
    path = tmp_path / "held.json"
    AssignmentRecord(path).accept([assignment("as_a"), assignment("as_b")])

    after_reboot = AssignmentRecord(path)

    assert after_reboot.held_ids() == ["as_a", "as_b"]


def test_every_field_of_an_assignment_survives_the_round_trip(tmp_path: Path) -> None:
    """Including the inline element set.

    A station reloading a record with no element set could not propagate the
    orbit, and MSP §4.3 carries it inline precisely so the station never has to
    fetch orbital data of its own.
    """
    path = tmp_path / "held.json"
    original = assignment("as_a")
    AssignmentRecord(path).accept([original])

    reloaded = AssignmentRecord(path).held()[0]

    assert reloaded == original


def test_the_record_is_written_before_accept_returns(tmp_path: Path) -> None:
    """The ordering that makes `held_assignments` truthful.

    An assignment reaches the file before it can reach the list. If the write
    failed, the caller never sees it, never names it, and the platform reads a
    decline — the right outcome, with no error path.
    """
    path = tmp_path / "held.json"
    record = AssignmentRecord(path)

    record.accept([assignment("as_a")])

    assert AssignmentRecord(path).held_ids() == ["as_a"]
    assert record.held_ids() == ["as_a"]


def test_dropping_closed_assignments_reports_which_went(tmp_path: Path) -> None:
    """So a station logs what it stopped holding rather than a shorter list."""
    path = tmp_path / "held.json"
    record = AssignmentRecord(path)
    record.accept(
        [
            assignment("as_finished", starts_in_minutes=-30),
            assignment("as_upcoming", starts_in_minutes=20),
        ]
    )

    dropped = record.drop_closed(NOW)

    assert [one.assignment_id for one in dropped] == ["as_finished"]
    assert record.held_ids() == ["as_upcoming"]
    assert AssignmentRecord(path).held_ids() == ["as_upcoming"]


def test_dropping_nothing_leaves_the_record_untouched(tmp_path: Path) -> None:
    """No write when nothing changed — a heartbeat every 30 seconds otherwise
    rewrites this file 2,880 times a day for no reason."""
    path = tmp_path / "held.json"
    record = AssignmentRecord(path)
    record.accept([assignment("as_upcoming")])
    written_at = path.stat().st_mtime_ns

    assert record.drop_closed(NOW) == ()
    assert path.stat().st_mtime_ns == written_at


def test_an_unreadable_record_is_refused_rather_than_read_as_empty(
    tmp_path: Path,
) -> None:
    """Empty and unreadable mean opposite things.

    Empty is a station that genuinely holds nothing. Unreadable is a station that
    may be holding a pass right now, and treating it as empty would silently
    decline work it had already accepted.
    """
    path = tmp_path / "held.json"
    path.write_text("[{broken", encoding="utf-8")

    with pytest.raises(MalformedAssignmentError, match="not a readable record"):
        AssignmentRecord(path)


def test_a_record_missing_a_field_is_refused(tmp_path: Path) -> None:
    """Same reasoning, one level down: a partial assignment is not an assignment."""
    path = tmp_path / "held.json"
    path.write_text('[{"assignment_id": "as_a"}]', encoding="utf-8")

    with pytest.raises(MalformedAssignmentError, match="missing"):
        AssignmentRecord(path)


def test_replacing_the_record_leaves_no_partial_file(tmp_path: Path) -> None:
    """The temporary file is moved into place, never left beside the real one."""
    path = tmp_path / "held.json"
    record = AssignmentRecord(path)
    record.accept([assignment("as_a")])
    record.accept([assignment("as_b")])

    assert [one.name for one in sorted(tmp_path.iterdir())] == ["held.json"]


def test_two_open_windows_resolve_to_the_one_that_started_first() -> None:
    """The scheduler does not issue overlapping windows to one station (D-065), so
    this is the case where something else went wrong — two configurations
    scheduled, or a clock stepped. Finishing the pass already under way beats
    abandoning it for one that just began, and either way the choice has to be
    the same on every tick or the station would thrash between them.
    """
    started_earlier = assignment("as_first", starts_in_minutes=-5, minutes_long=11)
    started_later = assignment("as_second", starts_in_minutes=-1, minutes_long=11)

    chosen = due_now((started_later, started_earlier), NOW)

    assert chosen is not None
    assert chosen.assignment_id == "as_first"


def test_nothing_is_due_before_a_window_opens() -> None:
    """A station must not start early: the platform already widened the window by
    its own timing uncertainty (D-021), so the start it was given is the start."""
    assert due_now((assignment("as_a", starts_in_minutes=5),), NOW) is None


def test_nothing_is_due_after_a_window_closes() -> None:
    """Symmetric, and the reason the loop can call this every tick unguarded."""
    finished = assignment("as_a", starts_in_minutes=-30, minutes_long=11)

    assert due_now((finished,), NOW) is None
