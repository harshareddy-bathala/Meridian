"""Which decided passes a round decides again, and when that writes anything (D-171)."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from meridian.scheduler import (
    Candidate,
    Commitment,
    Rejection,
    ScheduleOutcome,
    ScoredCandidate,
)
from meridian.scheduler.assignment_records import (
    PassFacts,
    Stamp,
    assignment_id_for,
    to_assignment_rows,
)
from meridian.scheduler.reissue import blocking, next_revision, reopens, unchanged
from meridian.store.schedule_reads import LatestDecision
from meridian.store.schedule_writes import NewAssignment

T0 = datetime(2026, 9, 28, tzinfo=UTC)


def latest(
    decision: str = "skipped",
    state: str = "issued",
    reason: str | None = None,
    blocker: str | None = "as_x",
    revision: int = 0,
) -> LatestDecision:
    return LatestDecision(
        pass_id=7,
        revision=revision,
        decision=decision,
        state=state,
        revoked_reason=reason,
        conflicts_with_assignment_id=blocker,
    )


@pytest.mark.parametrize(
    ("held", "open_"),
    [
        (None, True),
        (latest(), True),
        (latest("scheduled", "revoked", "offline", None), True),
        (latest("scheduled", "revoked", "declined", None), False),
        *[
            (latest("scheduled", state, None, None), False)
            for state in ("issued", "held", "in_progress", "reported", "expired")
        ],
    ],
)
def test_a_pass_reopens_when_skipped_or_taken_back_while_offline(
    held: LatestDecision | None, open_: bool
) -> None:
    assert reopens(held) is open_


def test_a_new_decision_is_the_next_revision() -> None:
    assert next_revision(None) == 0
    assert next_revision(latest(revision=2)) == 3


def row(decision: str, blocker: str | None) -> NewAssignment:
    return NewAssignment(
        assignment_id="as_new",
        pass_id=7,
        station_id="st_a",
        start_at=T0,
        end_at=T0 + timedelta(minutes=11),
        centre_freq_hz=137_100_000,
        mode="lrpt",
        timing_uncertainty_s=0.0,
        decision=decision,
        reason="",
        model_config="A",
        score=1.0,
        conflicts_with_assignment_id=blocker,
        priority=1.0,
        simulated=False,
    )


def test_only_a_skip_skipped_again_for_the_same_assignment_is_unchanged() -> None:
    assert unchanged(latest(blocker="as_x"), row("skipped", "as_x"))
    assert not unchanged(latest(blocker="as_x"), row("skipped", "as_y"))
    assert not unchanged(latest(blocker="as_x"), row("scheduled", None))
    assert not unchanged(
        latest("scheduled", "revoked", "offline", None), row("scheduled", None)
    )
    assert not unchanged(None, row("skipped", "as_x"))


def test_a_skip_whose_named_blocker_still_blocks_it_is_unchanged() -> None:
    """A later round may name another of the assignments in its way; the one
    the stored skip named being among them is the same decision."""
    assert unchanged(
        latest(blocker="as_x"), row("skipped", "as_y"), frozenset({"as_x"})
    )
    assert not unchanged(
        latest(blocker="as_x"), row("skipped", "as_y"), frozenset({"as_y", "as_z"})
    )
    assert not unchanged(
        latest(blocker="as_x"), row("scheduled", None), frozenset({"as_x"})
    )


def test_every_commitment_and_selection_in_a_skip_s_way_is_blocking_it() -> None:
    """One skip spanning an earlier commitment and a later selection; a third
    assignment an hour away is in nobody's way."""
    skip = ScoredCandidate(a_pass(3, 8), 5.0)
    selected = ScoredCandidate(a_pass(2, 16), 3.0)
    far = ScoredCandidate(a_pass(4, 60), 3.0)
    outcome = ScheduleOutcome(
        selected=[selected, far], rejected=[Rejection(skip, "overlap", 2)]
    )
    committed = [Commitment(a_pass(1, 0), "as_committed")]

    found = blocking(outcome, committed, {2: "as_two", 4: "as_four"}, 0.0)

    assert found == {3: frozenset({"as_committed", "as_two"})}


# --- ids by revision ----------------------------------------------------------


def test_revision_zero_mints_the_id_it_always_did() -> None:
    """No id stored before revisions existed may change."""
    legacy = "as_" + hashlib.sha256(b"41:A").hexdigest()[:12]

    assert assignment_id_for(41, "A") == assignment_id_for(41, "A", 0) == legacy
    assert assignment_id_for(41, "A", 1) == (
        "as_" + hashlib.sha256(b"41:A:1").hexdigest()[:12]
    )


def a_pass(pass_id: int, minute: float) -> Candidate:
    aos = T0 + timedelta(minutes=minute)
    return Candidate(
        pass_id=pass_id,
        station_id="st_a",
        aos=aos,
        los=aos + timedelta(minutes=11),
        margin_s=0.0,
        max_elevation_deg=40.0,
        priority=1.0,
        simulated=False,
    )


def test_a_skip_names_its_winner_by_the_winner_s_revision() -> None:
    """A pass decided again is a new row with a new id; what it displaced must
    name that id, or the foreign key names the revoked one."""
    winner = ScoredCandidate(a_pass(1, 0), 2.0)
    loser = ScoredCandidate(a_pass(2, 5), 1.0)
    outcome = ScheduleOutcome(
        selected=[winner], rejected=[Rejection(loser, "overlap", 1)]
    )
    facts = {
        one.candidate.pass_id: PassFacts(
            137_100_000, "lrpt", one.candidate.aos, one.candidate.los, 0.0
        )
        for one in (winner, loser)
    }
    stamp = Stamp(
        run_id="sr_x",
        model_sha256=None,
        explanations={1: {}, 2: {}},
        predicted_yields={},
        revisions={1: 1},
    )

    rows = {one.pass_id: one for one in to_assignment_rows(outcome, facts, "A", stamp)}

    assert (rows[1].assignment_id, rows[1].revision) == (
        assignment_id_for(1, "A", 1),
        1,
    )
    assert rows[2].conflicts_with_assignment_id == assignment_id_for(1, "A", 1)
    assert (rows[2].assignment_id, rows[2].revision) == (assignment_id_for(2, "A"), 0)
