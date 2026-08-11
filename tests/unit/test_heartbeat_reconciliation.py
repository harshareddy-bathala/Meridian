"""``reconcile`` against MSP §4.2's reconciliation table, row by row.

Every case below is two sets of assignment ids written out by hand, so the
expected transition is a comparison a reader can check without running anything.
The four rows of the specification's table are named in the test that covers
them, because the table is the requirement and a test that drifts from it should
be obvious.

Marked as a unit test by living in ``tests/unit``: no database, no clock and no
station.

Reference: docs/MSP-SPEC.md §4.2; docs/DECISIONS.md D-003, D-008, D-022, D-067.
"""

from __future__ import annotations

from meridian.registry.heartbeat_reconciliation import (
    HeldReport,
    LiveAssignments,
    Reconciliation,
    reconcile,
)


def platform_record(
    *,
    awaiting_hold: tuple[str, ...] = (),
    already_held: tuple[str, ...] = (),
    settled: tuple[str, ...] = (),
) -> LiveAssignments:
    """What the platform issued one station, with ``every_id`` derived.

    ``settled`` covers the states reconciliation never transitions out of —
    ``in_progress``, ``reported``, ``expired`` — which belong in ``every_id``
    because they were issued here even though nothing can move them.
    """
    return LiveAssignments(
        every_id=frozenset(awaiting_hold + already_held + settled),
        awaiting_hold=frozenset(awaiting_hold),
        already_held=frozenset(already_held),
    )


def station_says(*held: str, listening_on: str | None = None) -> HeldReport:
    """One heartbeat's claims, written as the station would state them."""
    return HeldReport(
        held_assignment_ids=frozenset(held), listening_assignment_id=listening_on
    )


def test_an_issued_assignment_the_station_lists_is_held() -> None:
    """MSP §4.2 row 1: issued, and present in the list."""
    result = reconcile(platform_record(awaiting_hold=("as_a",)), station_says("as_a"))

    assert result.to_hold == ("as_a",)
    assert result.to_start is None
    assert result.foreign_ids == ()


def test_an_issued_assignment_the_station_omits_is_left_alone() -> None:
    """MSP §4.2 row 2, under D-022: Phase 1 changes nothing.

    The decline is real and the platform records it by doing nothing — the
    assignment is offered again next heartbeat and expires after its window if it
    is never taken. There is no state meaning "taken back", so inventing a
    transition here would misreport when the decline happened.
    """
    result = reconcile(platform_record(awaiting_hold=("as_a",)), station_says())

    assert result == Reconciliation(to_hold=(), to_start=None, foreign_ids=())


def test_an_id_never_issued_to_this_station_is_foreign_and_moves_nothing() -> None:
    """MSP §4.2 row 4: log and ignore; do not act on it."""
    result = reconcile(
        platform_record(awaiting_hold=("as_a",)),
        station_says("as_a", "as_from_elsewhere"),
    )

    assert result.foreign_ids == ("as_from_elsewhere",)
    assert result.to_hold == ("as_a",)


def test_a_reported_assignment_the_station_still_lists_is_not_foreign() -> None:
    """Stale is not hostile.

    A station that keeps naming an assignment after the platform closed it has
    lagged, not misbehaved. Calling that a protocol error would fill the log with
    the one signal that is meant to mean a broken implementation.
    """
    result = reconcile(platform_record(settled=("as_done",)), station_says("as_done"))

    assert result.foreign_ids == ()
    assert result.to_hold == ()


def test_holding_is_idempotent_across_repeated_heartbeats() -> None:
    """The second heartbeat carries the same truth and asks for no new writes.

    This is what makes a lost heartbeat harmless: the statement is of current
    holdings, so replaying it converges rather than double-counting.
    """
    result = reconcile(platform_record(already_held=("as_a",)), station_says("as_a"))

    assert result.to_hold == ()
    assert result.foreign_ids == ()


def test_a_listening_block_starts_the_assignment_the_station_holds() -> None:
    """``held -> in_progress``, the transition D-008 ties to the listening block."""
    result = reconcile(
        platform_record(already_held=("as_a",)),
        station_says("as_a", listening_on="as_a"),
    )

    assert result.to_start == "as_a"


def test_a_station_may_confirm_and_begin_in_one_heartbeat() -> None:
    """Both transitions from one message, which is why ``to_hold`` applies first.

    A station whose pass opened between two heartbeats has genuinely done both,
    and refusing the second would delay `in_progress` by a whole interval — long
    enough on a 30-second poll to lose the opening of an 8-minute pass.
    """
    result = reconcile(
        platform_record(awaiting_hold=("as_a",)),
        station_says("as_a", listening_on="as_a"),
    )

    assert result.to_hold == ("as_a",)
    assert result.to_start == "as_a"


def test_listening_on_an_assignment_the_station_does_not_hold_starts_nothing() -> None:
    """A message contradicting itself is not evidence.

    ``in_progress`` means the station holds this work and is executing it. A
    listening block naming something absent from the same heartbeat's
    ``held_assignments`` supports neither half of that.
    """
    result = reconcile(
        platform_record(already_held=("as_a",)),
        station_says("as_a", listening_on="as_b"),
    )

    assert result.to_start is None


def test_listening_on_a_foreign_assignment_starts_nothing() -> None:
    """The protocol-error row does not become a transition by being listened on."""
    result = reconcile(
        platform_record(awaiting_hold=("as_a",)),
        station_says("as_a", "as_theirs", listening_on="as_theirs"),
    )

    assert result.to_start is None
    assert result.foreign_ids == ("as_theirs",)


def test_no_listening_block_starts_nothing() -> None:
    """An idle, slewing or processing station sends no block, and that is not a stop."""
    result = reconcile(platform_record(already_held=("as_a",)), station_says("as_a"))

    assert result.to_start is None


def test_an_empty_list_declines_everything_at_once() -> None:
    """§4.2: an empty list is meaningful and must be sent as ``[]``.

    It states that the station holds nothing — after a reboot, or because it
    refused the work. The platform's response is the same either way, which is
    the property D-003 built the mechanism for.
    """
    result = reconcile(
        platform_record(awaiting_hold=("as_a", "as_b"), already_held=("as_c",)),
        station_says(),
    )

    assert result == Reconciliation(to_hold=(), to_start=None, foreign_ids=())


def test_transitions_are_sorted_so_two_runs_read_alike() -> None:
    """Sets have no order, and an unordered id list makes a log diff unreadable."""
    result = reconcile(
        platform_record(awaiting_hold=("as_c", "as_a", "as_b")),
        station_says("as_b", "as_c", "as_a"),
    )

    assert result.to_hold == ("as_a", "as_b", "as_c")
