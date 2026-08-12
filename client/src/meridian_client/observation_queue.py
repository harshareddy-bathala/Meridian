"""Observations waiting to be delivered, kept on disk until the platform has them.

A pass is 8 to 15 minutes and never repeats, so a result that exists only in
memory is one an outage can destroy permanently. MSP §6 makes the queue part of
the protocol rather than an implementation nicety: a station that cannot reach
the platform keeps receiving and queues what it produced, and submits it later
with its original timestamps.

The rule this module exists to enforce, in one sentence: **an observation
reaches this directory before any request is attempted.** Delivery can then fail
any number of times without losing anything, because retrying is free — the
platform is idempotent on the assignment (D-015).

One file per observation, named for its assignment, holding exactly the MSP §4.4
body that will be sent. Reference: docs/MSP-SPEC.md §4.4, §6; docs/DECISIONS.md
D-015, D-068, D-073.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "FAILED_DIRECTORY",
    "MalformedQueueEntryError",
    "ObservationQueue",
    "QueuedObservation",
]

_log = logging.getLogger(__name__)

FAILED_DIRECTORY = "failed"
"""Where a payload goes when it can never be delivered.

Kept rather than deleted. A body the platform refuses outright is the evidence
of whatever produced it, and an operator with a station reporting nothing needs
to be able to look at one.
"""

SAFE_ASSIGNMENT_ID = re.compile(r"^[A-Za-z0-9_-]+$")
"""What may be used as a filename.

Assignment ids are minted by the platform as ``as_`` and twelve hex characters,
so this rejects nothing a real deployment produces. It is checked anyway because
the value arrives over the network and is about to become a path.
"""


class MalformedQueueEntryError(ValueError):
    """A file in the queue directory is not an observation this client wrote.

    A ``ValueError`` rather than a ``TypeError`` even when the shape is wrong:
    a ``TypeError`` says this program has a bug, and this says a file on disk
    is not what it should be. A station does not respond to those the same way —
    the same distinction ``MalformedAssignmentError`` draws for the held record.
    """


@dataclass(frozen=True, slots=True)
class QueuedObservation:
    """One observation waiting for delivery."""

    assignment_id: str
    body: Mapping[str, object]
    """The MSP §4.4 body, exactly as it will be sent."""


class ObservationQueue:
    """Observations on disk, oldest first.

    Args:
        directory: Where the queue lives. Created on first write, along with the
            ``failed`` directory beside it.

    Note:
        One file per observation rather than one file holding a list, which is
        where this differs from
        :class:`~meridian_client.held_assignments.AssignmentRecord`. An
        observation body reaches 256 KiB, and rewriting the whole queue on every
        acknowledgement would put every other queued result through a rewrite it
        did not need — with a power cut during any of them landing on all of
        them at once.
    """

    def __init__(self, directory: Path) -> None:
        """Open the queue at ``directory``. Nothing is read until it is asked for."""
        self._directory = directory

    def enqueue(self, body: Mapping[str, object]) -> None:
        """Write one observation down, before anything tries to send it.

        Args:
            body: An MSP §4.4 body from
                :func:`~meridian_client.observation_message.build_observation_body`.

        Raises:
            ValueError: The body carries no usable ``assignment_id``. Refused
                rather than filed under a generated name, because the id is how
                the acknowledgement will later be matched to this entry.
            OSError: The write failed. **Deliberately not caught**: the caller
                must not treat an observation as saved when it is not, and this
                is the one moment at which the result still exists in memory to
                be logged.

        Note:
            Re-queueing the same assignment overwrites the earlier file. That is
            the correction case, and the newer body is the station's current
            statement — the platform keeps both as revisions, which is where
            the history belongs (D-015).
        """
        assignment_id = _assignment_id_of(body)
        self._directory.mkdir(parents=True, exist_ok=True)
        _write_atomically(self._path_for(assignment_id), body)

    def pending(self) -> tuple[QueuedObservation, ...]:
        """Everything waiting, oldest first.

        Returns:
            The queued observations, ordered by the ``started_at`` they carry, so
            a backlog drains in the order the passes happened.

        Note:
            An unreadable entry is **moved to** ``failed/`` **and skipped**,
            not raised (D-073). This is the opposite of what the assignment
            record does with a corrupt file, and deliberately so: an assignment
            a station cannot read may be a pass it is supposed to be receiving
            right now, whereas a queue entry it cannot read is already lost —
            and refusing to go on would strand every *other* observation behind
            it, turning one damaged file into a station that reports nothing.
        """
        if not self._directory.exists():
            return ()

        readable = []
        for path in sorted(self._directory.glob("*.json")):
            entry = self._read_or_set_aside(path)
            if entry is not None:
                readable.append(entry)
        return tuple(sorted(readable, key=lambda one: str(one.body["started_at"])))

    def discard(self, assignment_id: str) -> None:
        """Forget an observation the platform has acknowledged.

        Args:
            assignment_id: The assignment whose entry to remove. Missing is not
                an error — an acknowledgement arriving twice is exactly the
                retry case the queue exists for.
        """
        self._path_for(assignment_id).unlink(missing_ok=True)

    def set_aside(self, assignment_id: str) -> None:
        """Move an entry the platform refused outright into ``failed/``.

        Args:
            assignment_id: The assignment whose entry to quarantine.

        Note:
            For a **permanent** refusal only — ``malformed``, ``not_owner``,
            ``unknown_assignment``. A transport failure or a ``5xx`` leaves the
            entry where it is, because the next tick will simply try again.
        """
        source = self._path_for(assignment_id)
        if not source.exists():
            return
        self._quarantine(source)

    def _quarantine(self, source: Path) -> None:
        """Move one file into ``failed/``, whatever its name is.

        Takes a path rather than an assignment id because the caller that needs
        it most is holding a file it could not read — and a file that cannot be
        read is exactly the one whose name may not be a valid assignment id.
        """
        destination = self._directory / FAILED_DIRECTORY
        destination.mkdir(parents=True, exist_ok=True)
        os.replace(source, destination / source.name)  # noqa: PTH105 — same call

    def _path_for(self, assignment_id: str) -> Path:
        """Where one assignment's entry lives."""
        if not SAFE_ASSIGNMENT_ID.match(assignment_id):
            raise ValueError(
                f"assignment_id is not usable as a filename: {assignment_id!r}"
            )
        return self._directory / f"{assignment_id}.json"

    def _read_or_set_aside(self, path: Path) -> QueuedObservation | None:
        """One entry, or ``None`` after quarantining a file that cannot be read."""
        try:
            body = _require_an_observation_body(json.loads(path.read_text("utf-8")))
            assignment_id = _assignment_id_of(body)
        except (OSError, ValueError) as exc:
            _log.error(
                "queued observation %s is unreadable, setting aside: %s", path, exc
            )
            # By path, not by `path.stem`: a file this method could not read is
            # also one whose name may not be an assignment id at all, and asking
            # for its entry by id would raise out of `pending` — turning the one
            # damaged file into the total reporting outage this exists to avoid.
            self._quarantine(path)
            return None
        return QueuedObservation(assignment_id=assignment_id, body=body)


def _require_an_observation_body(raw: object) -> dict[str, object]:
    """Check that a decoded file is shaped like the body this queue writes.

    ``started_at`` is the field :meth:`ObservationQueue.pending` orders by, so a
    file without one cannot take its place in the queue even if everything else
    about it is intact.
    """
    if not isinstance(raw, dict):
        raise MalformedQueueEntryError(f"not a JSON object: {type(raw).__name__}")
    if "started_at" not in raw:
        raise MalformedQueueEntryError("no started_at")
    return raw


def _assignment_id_of(body: Mapping[str, object]) -> str:
    """The assignment an observation body names."""
    assignment_id = body.get("assignment_id")
    if not isinstance(assignment_id, str) or not assignment_id:
        raise ValueError(f"observation body has no assignment_id: {assignment_id!r}")
    return assignment_id


def _write_atomically(path: Path, body: Mapping[str, object]) -> None:
    """Replace the entry in one step, so a power cut cannot truncate it.

    A half-written entry is an observation that survived the outage and is then
    thrown away by the code meant to deliver it — which is worse than never
    having queued it, because the station reports success and loses the result.
    """
    partial = path.with_name(path.name + ".partial")
    partial.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    os.replace(partial, path)  # noqa: PTH105 — Path.replace is the same call
