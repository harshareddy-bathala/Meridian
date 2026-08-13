"""Delivering what the upload queue holds, and deciding what a refusal means.

One pass over the queue, oldest first, stopping only when the platform turns out
not to be there. Separate from the loop because the interesting part is not the
sending — it is which failures mean *try again later*, which mean *never*, which
mean *step over this one*, and which mean *stop the station and tell someone*.

Holds no state: the queue is the state, and it is on disk. The one instant it
needs arrives as an argument.

Reference: docs/MSP-SPEC.md §4.4, §6; docs/DECISIONS.md D-015, D-024, D-073,
D-074.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx

from meridian_client.clock import parse_wire_time
from meridian_client.observation_message import (
    MalformedAcknowledgementError,
    parse_observation_ack,
)
from meridian_client.observation_queue import ObservationQueue, QueuedObservation
from meridian_client.transport import UNAUTHORIZED, MspTransport, ProtocolError

__all__ = [
    "PERMANENT_REFUSALS",
    "UNDELIVERABLE_AFTER",
    "SubmissionRun",
    "submit_pending",
]

_log = logging.getLogger(__name__)

UNDELIVERABLE_AFTER = timedelta(days=30)
"""How old an observation may be before the platform will not take it (D-013).

The platform refuses a ``started_at`` further back than this as ``malformed``,
so an entry past it is refused on every tick for the rest of the station's life.
Mirrored here rather than discovered by asking, the same way the Doppler cap is:
a payload that can never be delivered belongs in ``failed/``, not in a loop.
"""

PERMANENT_REFUSALS = frozenset({"malformed", "not_owner", "unknown_assignment"})
"""MSP §6 codes that will refuse this payload however often it is sent.

Retrying them is a station holding a body the platform has already judged, so
the payload is set aside for an operator instead. Everything else — a transport
failure, a ``429``, a ``5xx`` — leaves the observation queued for the next tick,
which is also what an outage looks like, so there is one path rather than two
(D-073).
"""


@dataclass(frozen=True, slots=True)
class SubmissionRun:
    """What one pass over the queue achieved."""

    submitted: tuple[str, ...]
    """The assignments the platform acknowledged, oldest first."""

    stop_reason: str | None = None
    """Set only when the station must stop entirely — today, a revoked token."""


def submit_pending(
    transport: MspTransport, queue: ObservationQueue, now: datetime
) -> SubmissionRun:
    """Send every queued observation the platform will take, oldest first.

    Args:
        transport: An authenticated transport.
        queue: The station's upload queue. Entries are removed as they are
            acknowledged, so an interrupted run resumes on the next call.
        now: The current instant, used only to tell an entry the platform's
            acceptance window has moved past. Passed in rather than read so the
            rule is testable at a fixed moment.

    Returns:
        The assignments acknowledged, and a stop reason if the loop must end.

    Note:
        **The drain stops only when the platform could not be reached** (D-074).
        A transport failure says the platform is not there, so every remaining
        entry would fail the same way and the tick ends at one request.

        Anything the platform *answered* — a refusal, or an acknowledgement the
        station cannot parse — is evidence about one exchange rather than about
        the platform's state, so the entry is left queued and the drain moves
        on. Without that, one payload the platform always rejects would stop the
        station reporting anything ever again, while its heartbeat went on
        saying it was listening.
    """
    submitted: list[str] = []
    for entry in queue.pending():
        if _has_outlived_the_platforms_window(entry, now):
            _set_aside_as_too_late(queue, entry)
            continue
        try:
            _submit_one(transport, queue, entry)
        except ProtocolError as exc:
            if exc.code == UNAUTHORIZED:
                return SubmissionRun(tuple(submitted), UNAUTHORIZED)
            if exc.code in PERMANENT_REFUSALS:
                _set_aside(queue, entry, exc)
                continue
            _log.warning("observation for %s refused: %s", entry.assignment_id, exc)
            continue
        except MalformedAcknowledgementError as exc:
            # Still queued: the station cannot tell whether the submission
            # landed, and asking again costs nothing because the platform is
            # idempotent on the assignment (D-015). The drain goes on, because
            # the platform answered — this says something about one exchange
            # rather than about its ability to take the next (D-074).
            _log.warning(
                "observation for %s not confirmed delivered: %s",
                entry.assignment_id,
                exc,
            )
            continue
        except httpx.HTTPError as exc:
            # The one failure that ends the tick. Nothing was refused and
            # nothing was answered — the platform is not there, so every
            # remaining entry would fail identically (D-074).
            _log.warning("the platform could not be reached: %s", exc)
            break
        submitted.append(entry.assignment_id)

    return SubmissionRun(tuple(submitted))


def _has_outlived_the_platforms_window(entry: QueuedObservation, now: datetime) -> bool:
    """Whether this entry is now too old for the platform to accept at all.

    An unreadable or missing ``started_at`` counts as outlived: it is the field
    the platform validates first, so an entry without one is refused every time
    it is sent, which is the same condition by a different route.
    """
    raw = entry.body.get("started_at")
    if not isinstance(raw, str):
        return True
    try:
        started_at = parse_wire_time(raw, "started_at")
    except ValueError:
        return True
    return started_at < now - UNDELIVERABLE_AFTER


def _set_aside_as_too_late(queue: ObservationQueue, entry: QueuedObservation) -> None:
    """Quarantine an entry the platform's own window has moved past."""
    _log.error(
        "the observation for %s is older than the %d days the platform accepts;"
        " it can no longer be delivered and is set aside for an operator (D-074)",
        entry.assignment_id,
        UNDELIVERABLE_AFTER.days,
    )
    queue.set_aside(entry.assignment_id)


def _set_aside(
    queue: ObservationQueue, entry: QueuedObservation, exc: ProtocolError
) -> None:
    """Quarantine a payload the platform will never take, loudly."""
    _log.error(
        "the platform refused the observation for %s (%s); setting it aside"
        " for an operator rather than retrying: %s",
        entry.assignment_id,
        exc.code,
        exc,
    )
    queue.set_aside(entry.assignment_id)


def _submit_one(
    transport: MspTransport, queue: ObservationQueue, entry: QueuedObservation
) -> None:
    """Send one queued observation and drop it once the platform confirms it.

    The order is load-bearing: the entry is discarded **after** a valid
    acknowledgement, never before. Discarding first would turn a lost response
    into a lost observation, while the opposite mistake — sending the same
    observation twice — costs nothing, because the platform appends a revision
    only when the content changed (D-015).
    """
    acknowledgement = parse_observation_ack(
        transport.observations(entry.body), entry.assignment_id
    )
    queue.discard(entry.assignment_id)
    _log.info(
        "observation %s accepted for %s%s",
        acknowledgement.observation_id,
        acknowledgement.assignment_id,
        ", superseding an earlier report" if acknowledgement.superseded else "",
    )
