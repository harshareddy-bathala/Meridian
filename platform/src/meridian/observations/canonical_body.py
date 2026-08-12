"""The canonical form of an observation, and the digest taken over it.

Turns a :class:`~meridian.store.observations.NewObservation` into one byte string
that depends on the observation's *content* and nothing else, so that two
submissions carrying the same facts produce the same digest however the station
happened to serialise them. :mod:`meridian.observations.ingest` compares that
digest against the current revision to tell a retry from a correction (D-015).

Pure computation: no I/O, no database, no clock. Everything it needs is in the
record it is handed, which is what makes the whole idempotency rule testable
without infrastructure.

The digest is a platform-internal value. It appears in no MSP message and no
station ever computes one, which is why this module can define its own
canonical form rather than adopting a cross-language standard (D-070).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from meridian.store.observations import DopplerSample, NewObservation

__all__ = ["canonical_bytes", "content_sha256"]


def content_sha256(record: NewObservation) -> bytes:
    """The digest stored in ``observations.content_sha256``.

    Args:
        record: The derived observation, after ``satellite_id`` and
            ``simulated`` have been resolved from the platform's own records.

    Returns:
        Thirty-two raw bytes, for the ``bytea`` column. Not hex: the column is
        binary, and hex would double the width of every row for readability
        nobody reading a hypertable needs.
    """
    return hashlib.sha256(canonical_bytes(record)).digest()


def canonical_bytes(record: NewObservation) -> bytes:
    """Render an observation to the one byte string its digest is taken over.

    Args:
        record: The derived observation.

    Returns:
        UTF-8 JSON with sorted keys and no whitespace.

    Raises:
        ValueError: A float in the record is not finite. JSON has no literal for
            ``NaN`` or infinity, but a parser hands one back for input like
            ``1e400``, and a digest over a value the column cannot faithfully
            hold would be a hash of something that was never stored.

    Note:
        Three of the rules exist to make *the same facts* hash the same way.
        Keys are sorted, so field order on the wire is irrelevant. Timestamps
        are normalised to UTC and truncated to milliseconds, so a station
        reporting ``+05:30`` and one reporting ``Z`` agree, and so does one whose
        clock has microsecond resolution. Absent fields are written as ``null``
        rather than omitted, which needs no rule at all — they arrive here as
        ``None`` either way, because this renders the record rather than the
        request.

        One rule exists to make different facts hash differently: **array order
        is preserved**. ``doppler_samples`` is a time series, so two orderings
        are two different measurements, and sorting them would make a scrambled
        upload indistinguishable from the original.

        ``revision``, ``submitted_at`` and ``observation_id`` are excluded and
        are not fields of the record for that reason — they are assigned by the
        platform, so including them would make every retry look like a change.
    """
    return json.dumps(
        _canonical_mapping(record),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _canonical_mapping(record: NewObservation) -> dict[str, object]:
    """The record as plain JSON values, before serialisation."""
    return {
        "assignment_id": record.assignment_id,
        "station_id": record.station_id,
        "satellite_id": record.satellite_id,
        "started_at": _instant(record.started_at),
        "ended_at": _instant(record.ended_at),
        "outcome": record.outcome,
        "signal_detected": record.signal_detected,
        "first_detection_at": _optional_instant(record.first_detection_at),
        "peak_snr_db": record.peak_snr_db,
        "doppler_samples": _samples(record.doppler_samples),
        "products": [dict(one) for one in record.products],
        "client_notes": record.client_notes,
        "simulated": record.simulated,
    }


def _samples(
    samples: Sequence[DopplerSample] | None,
) -> list[Mapping[str, object]] | None:
    """The Doppler array in submitted order, or ``None`` when none was sent.

    ``None`` and ``[]`` stay distinct: a station with no frequency reference
    sends nothing, and one that measured throughout a pass and found no samples
    worth reporting sent an empty array. They are different claims.
    """
    if samples is None:
        return None
    return [
        {"t": _instant(one.sampled_at), "offset_hz": one.offset_hz} for one in samples
    ]


def _optional_instant(instant: datetime | None) -> str | None:
    """:func:`_instant`, passing ``None`` through unchanged."""
    if instant is None:
        return None
    return _instant(instant)


def _instant(instant: datetime) -> str:
    """One timestamp, as UTC with millisecond precision and a ``Z`` suffix.

    Deliberately this module's own rendering rather than the API's, although the
    two produce the same string today. The API's belongs to the wire and may
    change with the protocol; this one belongs to the digest, and changing it
    would make every stored hash disagree with the record it was taken over.

    Raises:
        ValueError: The instant is naive. A naive datetime here would be hashed
            as though it were UTC, so two stations in different zones reporting
            different moments would collide.
    """
    if instant.tzinfo is None:
        raise ValueError("observation timestamps must be timezone-aware UTC")
    return (
        instant.astimezone(UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
