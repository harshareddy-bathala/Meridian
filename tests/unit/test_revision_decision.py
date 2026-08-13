"""``decide_revision`` — the append-or-not rule, away from any database.

D-015's whole idempotency contract is four cases, and all four are decidable
from the current revision and one digest. No marker: this needs no
infrastructure, which is why the rule was separated from the write in the first
place.
"""

from __future__ import annotations

from meridian.observations.ingest import decide_revision
from meridian.store.observations import LatestObservation

FIRST_DIGEST = b"\xaa" * 32
SECOND_DIGEST = b"\xbb" * 32

REVISION_ONE = LatestObservation(
    revision=1, content_sha256=FIRST_DIGEST, observation_id="ob_05601bd09768"
)


def test_a_first_report_is_revision_one() -> None:
    """Nothing to supersede, so nothing is reported as superseded."""
    decision = decide_revision(None, FIRST_DIGEST)

    assert decision.revision == 1
    assert not decision.superseded
    assert decision.existing_observation_id is None


def test_an_unchanged_retry_writes_nothing_and_reuses_the_original_id() -> None:
    """MSP §6's queued-reconnection case, where the station never saw the answer.

    `superseded` stays false: from the station's side nothing happened, and the
    acknowledgement it finally receives is the one it missed the first time.
    """
    decision = decide_revision(REVISION_ONE, FIRST_DIGEST)

    assert decision.revision == 1
    assert not decision.superseded
    assert decision.existing_observation_id == "ob_05601bd09768"


def test_a_changed_body_appends_the_next_revision() -> None:
    """A correction — the case that makes the table append-only rather than fixed."""
    decision = decide_revision(REVISION_ONE, SECOND_DIGEST)

    assert decision.revision == 2
    assert decision.superseded
    assert decision.existing_observation_id is None


def test_resubmitting_a_withdrawn_body_appends_rather_than_reviving() -> None:
    """The awkward case, and the one most likely to be argued with at review.

    Revision 1 said one thing, revision 2 corrected it, and the station now
    re-sends the revision 1 body. It differs from *current*, so it becomes
    revision 3 rather than being recognised as something already on file.

    Comparing against the whole lineage is the alternative and is worse: the
    station's latest statement would lose to one it had already withdrawn, and
    the platform would answer with an id belonging to a revision that is no
    longer current. Append-only means revision 1 is still there to show what
    happened either way.
    """
    revision_two = LatestObservation(
        revision=2, content_sha256=SECOND_DIGEST, observation_id="ob_second"
    )

    decision = decide_revision(revision_two, FIRST_DIGEST)

    assert decision.revision == 3
    assert decision.superseded
    assert decision.existing_observation_id is None


def test_only_an_unchanged_retry_declines_to_write() -> None:
    """Across all three cases, exactly one answers without appending a row.

    Stated as a property rather than three separate assertions because it is the
    invariant the caller relies on: it writes when, and only when, it has no id
    to hand back.
    """
    declined = [
        decide_revision(latest, digest).existing_observation_id is not None
        for latest, digest in (
            (None, FIRST_DIGEST),
            (REVISION_ONE, FIRST_DIGEST),
            (REVISION_ONE, SECOND_DIGEST),
        )
    ]

    assert declined == [False, True, False]
