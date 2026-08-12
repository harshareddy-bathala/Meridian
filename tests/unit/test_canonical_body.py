"""``meridian.observations.canonical_body`` — D-070's rules, one test each.

No marker, no infrastructure: the module is pure computation, which is the whole
reason the idempotency rule can be pinned down here rather than against a
database.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from meridian.observations.canonical_body import canonical_bytes, content_sha256
from meridian.store.observations import DopplerSample, NewObservation

STARTED_AT = datetime(2026, 8, 14, 9, 41, 18, tzinfo=UTC)
ENDED_AT = datetime(2026, 8, 14, 9, 52, 10, tzinfo=UTC)
DETECTED_AT = datetime(2026, 8, 14, 9, 41, 53, tzinfo=UTC)

INDIAN_STANDARD_TIME = timezone(timedelta(hours=5, minutes=30))
"""Where this project's station is. A real offset rather than a contrived one."""


def observation(**overrides: Any) -> NewObservation:
    """MSP §4.4's example observation, as the record the platform derives."""
    fields: dict[str, Any] = {
        "assignment_id": "as_44b2",
        "station_id": "st_7fa3c1",
        "satellite_id": "norad:57166",
        "started_at": STARTED_AT,
        "ended_at": ENDED_AT,
        "outcome": "decoded",
        "signal_detected": True,
        "first_detection_at": DETECTED_AT,
        "peak_snr_db": 11.4,
        "doppler_samples": (
            DopplerSample(DETECTED_AT, 3140),
            DopplerSample(datetime(2026, 8, 14, 9, 46, 44, tzinfo=UTC), 12),
        ),
        "products": ({"kind": "waterfall", "uri": "file:///w.png"},),
        "client_notes": "rotator lagged 2s at AOS",
        "simulated": False,
    }
    fields.update(overrides)
    return NewObservation(**fields)


def test_the_rendering_is_sorted_and_has_no_whitespace() -> None:
    """The two properties that make the byte string canonical at all."""
    # No spaces inside any value, so a bare `" " not in` says what it looks like.
    rendered = canonical_bytes(observation(client_notes=None)).decode("utf-8")

    assert " " not in rendered
    keys = list(json.loads(rendered))
    assert keys == sorted(keys)


def test_the_same_instant_in_another_zone_hashes_the_same() -> None:
    """A station reporting +05:30 and one reporting Z described one moment.

    This is the case a raw-bytes hash gets wrong: the two bodies differ by
    several characters and mean exactly the same thing.
    """
    as_utc = observation()
    as_local = observation(started_at=STARTED_AT.astimezone(INDIAN_STANDARD_TIME))

    assert content_sha256(as_utc) == content_sha256(as_local)


def test_sub_millisecond_precision_does_not_change_the_digest() -> None:
    """MSP promises milliseconds; a finer clock is not a different observation."""
    coarse = observation()
    fine = observation(started_at=STARTED_AT.replace(microsecond=400))

    assert content_sha256(coarse) == content_sha256(fine)


def test_a_changed_measurement_changes_the_digest() -> None:
    """The digest has to notice a correction, or D-015 never appends a revision."""
    original = observation()
    corrected = observation(peak_snr_db=9.1)

    assert content_sha256(original) != content_sha256(corrected)


def test_a_changed_outcome_changes_the_digest() -> None:
    """The field most likely to be corrected after a decoder is re-run."""
    assert content_sha256(observation()) != content_sha256(
        observation(outcome="signal_no_decode")
    )


def test_reordering_doppler_samples_changes_the_digest() -> None:
    """Array order is content, not presentation.

    The samples are a time series. Two orderings are two different measurements,
    and a canonicaliser that sorted them would make a scrambled upload
    indistinguishable from the original — which is exactly the corruption the
    hash exists to catch.
    """
    forwards = observation()
    backwards = observation(doppler_samples=tuple(reversed(forwards.doppler_samples)))

    assert content_sha256(forwards) != content_sha256(backwards)


def test_no_samples_and_an_empty_array_are_different_claims() -> None:
    """`None` is a station without a frequency reference; `[]` is one that measured."""
    assert content_sha256(observation(doppler_samples=None)) != content_sha256(
        observation(doppler_samples=())
    )


def test_an_absent_optional_field_renders_as_null() -> None:
    """Omitted and explicit `null` reach the record identically, so both hash alike.

    There is no rule here to get wrong — the record has one representation for
    "not present", and canonicalising the record rather than the request is what
    makes that automatic.
    """
    rendered = json.loads(canonical_bytes(observation(client_notes=None)))

    assert "client_notes" in rendered
    assert rendered["client_notes"] is None


def test_the_platform_assigned_fields_are_absent_from_the_rendering() -> None:
    """A retry must hash the same as the submission it repeats.

    `revision` and `submitted_at` differ between the two by construction, so a
    rendering that carried them would make every retry look like a correction —
    which is the exact behaviour D-015 exists to prevent.
    """
    rendered = json.loads(canonical_bytes(observation()))

    assert not {"revision", "submitted_at", "observation_id"} & set(rendered)


def test_a_naive_timestamp_is_refused() -> None:
    """A naive datetime would be hashed as though it were UTC."""
    with pytest.raises(ValueError, match="timezone-aware"):
        canonical_bytes(observation(started_at=datetime(2026, 8, 14, 9, 41, 18)))  # noqa: DTZ001


def test_a_non_finite_measurement_is_refused() -> None:
    """JSON has no `NaN` literal, but a parser returns one for `1e400`.

    Hashing it would record a digest over a value `double precision` could not
    faithfully hold, so the observation is refused before it reaches the column.
    """
    with pytest.raises(ValueError, match=r"Out of range|not JSON compliant"):
        canonical_bytes(observation(peak_snr_db=float("inf")))


def test_the_digest_is_thirty_two_raw_bytes() -> None:
    """The shape `observations.content_sha256` stores."""
    digest = content_sha256(observation())

    assert isinstance(digest, bytes)
    assert len(digest) == 32


def test_two_stations_reporting_identically_do_not_collide() -> None:
    """`station_id` is in the rendering, so one station's row is not another's.

    Two stations can observe the same satellite over the same window with the
    same result. They are different observations of different assignments, and
    the digest has to say so.
    """
    ours = observation()
    theirs = observation(assignment_id="as_other", station_id="st_other")

    assert content_sha256(ours) != content_sha256(theirs)
