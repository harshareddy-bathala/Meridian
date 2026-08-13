"""``meridian_client.observation_queue`` — durability, in a temporary directory.

No marker: this needs a filesystem and nothing else. The properties under test
are the ones Stage 9's completion gate rests on — a completed result cannot be
lost, and a restart changes nothing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meridian_client.observation_queue import (
    FAILED_DIRECTORY,
    ObservationQueue,
    QueuedObservation,
)

STATION_ID = "st_7fa3c1"


def body(
    assignment_id: str, started_at: str = "2026-08-14T09:41:18Z"
) -> dict[str, object]:
    """A minimal MSP §4.4 body, with the two fields the queue itself reads."""
    return {
        "assignment_id": assignment_id,
        "station_id": STATION_ID,
        "started_at": started_at,
        "ended_at": "2026-08-14T09:52:10Z",
        "outcome": "decoded",
    }


def test_an_observation_survives_the_process_that_queued_it(tmp_path: Path) -> None:
    """The completion gate, at its smallest: a restart loses nothing.

    A second queue is constructed over the same directory, standing in for the
    station coming back after a crash.
    """
    ObservationQueue(tmp_path).enqueue(body("as_44b2"))

    after_restart = ObservationQueue(tmp_path).pending()

    assert [one.assignment_id for one in after_restart] == ["as_44b2"]
    assert after_restart[0].body["outcome"] == "decoded"


def test_an_empty_directory_holds_nothing(tmp_path: Path) -> None:
    """A station that has produced nothing yet is not an error."""
    assert ObservationQueue(tmp_path / "never-written").pending() == ()


def test_a_backlog_drains_in_the_order_the_passes_happened(tmp_path: Path) -> None:
    """Oldest first, by `started_at` rather than by filename or by write order.

    After an outage the queue holds several passes, and the platform's
    submission-delay figures are easiest to read when they arrive in the order
    they occurred.
    """
    queue = ObservationQueue(tmp_path)
    queue.enqueue(body("as_third", "2026-08-14T12:00:00Z"))
    queue.enqueue(body("as_first", "2026-08-14T09:00:00Z"))
    queue.enqueue(body("as_second", "2026-08-14T10:30:00Z"))

    assert [one.assignment_id for one in queue.pending()] == [
        "as_first",
        "as_second",
        "as_third",
    ]


def test_discarding_removes_exactly_one_entry(tmp_path: Path) -> None:
    queue = ObservationQueue(tmp_path)
    queue.enqueue(body("as_kept"))
    queue.enqueue(body("as_acknowledged"))

    queue.discard("as_acknowledged")

    assert [one.assignment_id for one in queue.pending()] == ["as_kept"]


def test_discarding_something_already_gone_is_not_an_error(tmp_path: Path) -> None:
    """An acknowledgement arriving twice is the retry case the queue exists for."""
    queue = ObservationQueue(tmp_path)
    queue.enqueue(body("as_44b2"))
    queue.discard("as_44b2")

    queue.discard("as_44b2")

    assert queue.pending() == ()


def test_requeueing_an_assignment_replaces_its_entry(tmp_path: Path) -> None:
    """A correction is the station's newer statement, not a second queue entry.

    The platform keeps both as revisions, which is where the history belongs.
    """
    queue = ObservationQueue(tmp_path)
    queue.enqueue(body("as_44b2"))
    corrected = body("as_44b2")
    corrected["outcome"] = "signal_no_decode"

    queue.enqueue(corrected)

    pending = queue.pending()
    assert len(pending) == 1
    assert pending[0].body["outcome"] == "signal_no_decode"


def test_no_partial_file_is_ever_left_beside_the_real_one(tmp_path: Path) -> None:
    """Temp-then-rename, so a power cut cannot truncate an entry (D-068)."""
    ObservationQueue(tmp_path).enqueue(body("as_44b2"))

    assert [one.name for one in sorted(tmp_path.iterdir())] == ["as_44b2.json"]


def test_a_setaside_entry_is_kept_out_of_the_queue_but_kept_on_disk(
    tmp_path: Path,
) -> None:
    """A permanently refused payload is evidence, so it is moved and not deleted."""
    queue = ObservationQueue(tmp_path)
    queue.enqueue(body("as_refused"))

    queue.set_aside("as_refused")

    assert queue.pending() == ()
    assert (tmp_path / FAILED_DIRECTORY / "as_refused.json").exists()


def test_an_unreadable_entry_is_set_aside_and_the_rest_still_drain(
    tmp_path: Path,
) -> None:
    """D-073: one damaged file must not stop a station reporting anything at all.

    This is deliberately the opposite of what the assignment record does with a
    corrupt file. That one may describe a pass in progress; this one is an
    observation already lost, and stranding the others behind it would turn one
    bad file into a permanent reporting outage.
    """
    queue = ObservationQueue(tmp_path)
    queue.enqueue(body("as_good"))
    (tmp_path / "as_truncated.json").write_text(
        '{"assignment_id": "as_tru', encoding="utf-8"
    )

    pending = queue.pending()

    assert [one.assignment_id for one in pending] == ["as_good"]
    assert (tmp_path / FAILED_DIRECTORY / "as_truncated.json").exists()


def test_a_stray_file_whose_name_is_not_an_assignment_id_still_drains(
    tmp_path: Path,
) -> None:
    """Quarantining is by path, so an odd filename cannot strand the queue.

    An operator copying a note into the outbox, or any tool leaving a file with
    a dot in its stem, must not stop the station reporting. Setting the entry
    aside by ``path.stem`` would ask for an entry by an id that is not one, and
    the refusal would escape ``pending`` and take the loop with it.
    """
    queue = ObservationQueue(tmp_path)
    queue.enqueue(body("as_good"))
    (tmp_path / "operator.notes.json").write_text("{}", encoding="utf-8")

    pending = queue.pending()

    assert [one.assignment_id for one in pending] == ["as_good"]
    assert (tmp_path / FAILED_DIRECTORY / "operator.notes.json").exists()


def test_an_entry_that_is_json_but_not_an_observation_is_set_aside(
    tmp_path: Path,
) -> None:
    """Valid JSON is not the same as a body this client wrote."""
    queue = ObservationQueue(tmp_path)
    (tmp_path / "as_wrong.json").write_text(json.dumps([1, 2, 3]), encoding="utf-8")

    assert queue.pending() == ()
    assert (tmp_path / FAILED_DIRECTORY / "as_wrong.json").exists()


def test_a_body_without_an_assignment_id_is_never_queued(tmp_path: Path) -> None:
    """The id is how the acknowledgement will be matched back to this entry.

    Filed under a generated name it would be undeliverable and unmatchable, so
    the caller is told at the one moment the result still exists in memory.
    """
    with pytest.raises(ValueError, match="assignment_id"):
        ObservationQueue(tmp_path).enqueue({"started_at": "2026-08-14T09:41:18Z"})


def test_an_assignment_id_that_is_not_a_safe_filename_is_refused(
    tmp_path: Path,
) -> None:
    """The id arrives over the network and is about to become a path."""
    with pytest.raises(ValueError, match="filename"):
        ObservationQueue(tmp_path).enqueue(body("../../etc/passwd"))


def test_the_queued_body_is_the_body_that_will_be_sent(tmp_path: Path) -> None:
    """The file format is the wire format (D-068), so there is only one parser.

    Asserted against the file on disk rather than through `pending`, because the
    claim is about the bytes rather than about the round trip.
    """
    original = body("as_44b2")
    ObservationQueue(tmp_path).enqueue(original)

    stored = json.loads((tmp_path / "as_44b2.json").read_text(encoding="utf-8"))

    assert stored == original


def test_pending_returns_what_a_caller_can_submit(tmp_path: Path) -> None:
    """The entry carries both the id to acknowledge against and the body to post."""
    queue = ObservationQueue(tmp_path)
    queue.enqueue(body("as_44b2"))

    (entry,) = queue.pending()

    assert isinstance(entry, QueuedObservation)
    assert entry.assignment_id == "as_44b2"
    assert entry.body["station_id"] == STATION_ID
