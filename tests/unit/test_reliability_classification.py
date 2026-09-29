"""``meridian.reliability.classification`` — the one definition of a miss.

One case per rule, then one per place two rules could both claim a pass, then
D-147's verdict. The snapshot labeller's own tests cover the same rules
through its rows; these cover them at the function both paths call.

Reference: docs/DECISIONS.md D-146, D-147, D-171, D-180, D-181.
"""

from __future__ import annotations

import pytest

from meridian.reliability.classification import (
    CAPTURED,
    MISS,
    PASS_CLASSES,
    SATELLITE_CLASSES,
    PassEvidence,
    classify,
)
from meridian.reliability.satellite_silence import SatelliteState, judge_satellite


def evidence(
    outcome: str | None = "no_signal",
    *,
    states: tuple[str, ...] = ("reported",),
    heard: bool = True,
    listening: bool | None = True,
    revoked: tuple[str, ...] = (),
) -> PassEvidence:
    return PassEvidence(
        outcome=outcome,
        assignment_states=states,
        heard_during_window=heard,
        listening_confirmed=listening,
        revoked_reasons=revoked,
    )


def never_asked() -> SatelliteState:
    raise AssertionError("the satellite's state was asked for when it did not matter")


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        ("decoded", "successful_reception"),
        ("signal_no_decode", "signal_no_decode"),
        ("aborted", "station_unavailable"),
        ("not_attempted", "station_unavailable"),
    ],
)
def test_a_report_with_an_outcome_of_its_own_says_what_happened(
    outcome: str, expected: str
) -> None:
    """Even with no heartbeat and no listening: the report is the evidence."""
    got = classify(evidence(outcome, heard=False, listening=None), never_asked)

    assert got == expected


@pytest.mark.parametrize("outcome", ["no_signal", None])
def test_silence_with_no_heartbeat_is_a_station_that_was_not_there(
    outcome: str | None,
) -> None:
    assert classify(evidence(outcome, heard=False), never_asked) == (
        "station_unavailable"
    )


def test_an_expiry_while_the_station_was_heard_is_a_decline() -> None:
    got = classify(evidence(None, states=("expired",), listening=None), never_asked)

    assert got == "assignment_declined"


def test_an_expiry_while_nobody_was_heard_is_not_a_decline() -> None:
    """D-181: the station may never have seen the assignment it did not take."""
    got = classify(evidence(None, states=("expired",), heard=False), never_asked)

    assert got == "station_unavailable"


def test_one_assignment_still_open_is_not_a_decline() -> None:
    """A pass is declined only if every assignment of it expired."""
    got = classify(
        evidence(None, states=("expired", "held"), listening=False), never_asked
    )

    assert got == "station_not_confirmed_listening"


@pytest.mark.parametrize("listening", [False, None], ids=["answered-no", "not-asked"])
def test_silence_the_registry_did_not_confirm_is_not_a_miss(
    listening: bool | None,
) -> None:
    """Rule 7 of CLAUDE.md, the reason this module exists."""
    got = classify(evidence(listening=listening), never_asked)

    assert got == "station_not_confirmed_listening"
    assert got != MISS


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("transmitting", "confirmed_miss"),
        ("silent", "satellite_silent"),
        ("indeterminate", "satellite_state_indeterminate"),
    ],
)
def test_confirmed_silence_is_judged_by_the_satellite(
    state: SatelliteState, expected: str
) -> None:
    assert classify(evidence(), lambda: state) == expected


@pytest.mark.parametrize("outcome", ["no_signal", None])
def test_confirmed_listening_with_or_without_a_report_reaches_the_satellite(
    outcome: str | None,
) -> None:
    """A report lost in a queue that never arrived is still silence heard."""
    assert classify(evidence(outcome), lambda: "transmitting") == MISS


def test_a_pass_nothing_scheduled_cannot_be_classified() -> None:
    with pytest.raises(ValueError, match="nothing scheduled"):
        evidence(states=())


def test_every_class_is_listed_once_and_the_subsets_are_classes() -> None:
    assert len(set(PASS_CLASSES)) == len(PASS_CLASSES) == 8
    assert CAPTURED | SATELLITE_CLASSES | {MISS} <= set(PASS_CLASSES)
    assert not CAPTURED & SATELLITE_CLASSES


@pytest.mark.parametrize(
    ("signals", "silences", "expected"),
    [
        (1, 0, "transmitting"),
        (1, 5, "transmitting"),
        (0, 2, "silent"),
        (0, 3, "silent"),
        (0, 1, "indeterminate"),
        (0, 0, "indeterminate"),
    ],
)
def test_one_signal_outweighs_any_number_of_silences(
    signals: int, silences: int, expected: str
) -> None:
    got = judge_satellite(signals=signals, silences=silences, min_silent_attempts=2)

    assert got == expected


# --- taken back before the window (D-171) -------------------------------------


@pytest.mark.parametrize(
    ("reasons", "expected"),
    [
        (("declined",), "assignment_declined"),
        (("offline",), "station_unavailable"),
        (("offline", "declined"), "assignment_declined"),
    ],
)
def test_work_taken_back_is_never_a_miss(
    reasons: tuple[str, ...], expected: str
) -> None:
    """Even heard and confirmed listening: nothing was asked of the station."""
    got = classify(evidence(None, states=(), revoked=reasons), never_asked)

    assert got == expected


def test_a_reissue_left_with_the_station_decides_the_pass() -> None:
    """A revoked sibling does not make the live assignment look declined."""
    got = classify(
        evidence(None, states=("issued",), revoked=("declined",), listening=False),
        never_asked,
    )

    assert got == "station_not_confirmed_listening"


def test_a_report_on_a_revoked_assignment_is_still_a_reception() -> None:
    got = classify(evidence("decoded", states=(), revoked=("offline",)), never_asked)

    assert got == "successful_reception"
