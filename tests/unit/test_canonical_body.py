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
from meridian.store.observations import (
    DecodeStatistics,
    DopplerSample,
    NewObservation,
    SnrSample,
)

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


MSP_02_EXAMPLE_DIGEST = (
    "0ff3ad44f9db3299a1be48e6fc508d8155e6d05c1e07ea5a4c9661f5d84d5c78"
)
MSP_02_NOT_ATTEMPTED_DIGEST = (
    "eb6f5ba0e4ce63264b7b0fde1ad73fb86c1a86d9b4d39b87c678ed5bc14bc2eb"
)


def test_a_02_observation_keeps_the_digest_it_was_stored_with() -> None:
    """Every stored `content_sha256` must keep matching its own row (D-118).

    Pinned from the rendering as it was before MSP 0.3's fields existed. A field
    added later that changed these bytes would make every stored hash disagree
    with the record it was taken over, and turn every queued 0.2 retry into a
    spurious new revision. The second observation has every optional field
    absent — the case a naive `null` rendering of the new fields would change.
    """
    assert content_sha256(observation()).hex() == MSP_02_EXAMPLE_DIGEST
    assert (
        content_sha256(
            observation(
                outcome="not_attempted",
                signal_detected=False,
                first_detection_at=None,
                peak_snr_db=None,
                doppler_samples=None,
                products=(),
                client_notes=None,
            )
        ).hex()
        == MSP_02_NOT_ATTEMPTED_DIGEST
    )


def test_two_stations_reporting_identically_do_not_collide() -> None:
    """`station_id` is in the rendering, so one station's row is not another's.

    Two stations can observe the same satellite over the same window with the
    same result. They are different observations of different assignments, and
    the digest has to say so.
    """
    ours = observation()
    theirs = observation(assignment_id="as_other", station_id="st_other")

    assert content_sha256(ours) != content_sha256(theirs)


LATER = datetime(2026, 8, 14, 9, 46, 44, tzinfo=UTC)
EVIDENCE: dict[str, Any] = {
    "noise_floor_dbfs": -52.3,
    "receiver_gain_db": 32.8,
    "snr_samples": (SnrSample(DETECTED_AT, 3.1), SnrSample(LATER, 11.4)),
    "decode": DecodeStatistics("satdump", "1.2.2", 412, 37),
}
"""MSP 0.3's §4.4 example evidence, as the record carries it."""


def test_evidence_that_was_not_measured_is_absent_from_the_rendering() -> None:
    """D-118: a key added after 0.2 appears only when it holds a value.

    Rendered as `null`, these would change the bytes of every observation stored
    before 0.3 — which the pinned 0.2 digests above would also catch, from the
    other side.
    """
    rendered = json.loads(canonical_bytes(observation()))

    assert not {"noise_floor_dbfs", "receiver_gain_db", "snr_samples", "decode"} & set(
        rendered
    )


def test_measured_evidence_is_rendered() -> None:
    rendered = json.loads(canonical_bytes(observation(**EVIDENCE)))

    assert rendered["noise_floor_dbfs"] == -52.3
    assert rendered["receiver_gain_db"] == 32.8
    assert rendered["snr_samples"] == [
        {"t": "2026-08-14T09:41:53.000Z", "snr_db": 3.1},
        {"t": "2026-08-14T09:46:44.000Z", "snr_db": 11.4},
    ]
    assert rendered["decode"] == {
        "decoder": "satdump",
        "decoder_version": "1.2.2",
        "frames_decoded": 412,
        "frames_failed": 37,
    }


def test_adding_evidence_to_a_report_changes_its_digest() -> None:
    """A 0.3 correction of a 0.2 report is a new revision, not a retry."""
    assert content_sha256(observation()) != content_sha256(observation(**EVIDENCE))


def test_a_changed_frame_count_changes_the_digest() -> None:
    """The statistic most likely to change when a recording is decoded again."""
    recounted = EVIDENCE | {"decode": DecodeStatistics("satdump", "1.2.2", 400, 49)}

    assert content_sha256(observation(**EVIDENCE)) != content_sha256(
        observation(**recounted)
    )


def test_no_snr_samples_and_an_empty_array_are_different_claims() -> None:
    """As for Doppler: not measured is not the same as measured and empty."""
    assert content_sha256(observation(snr_samples=None)) != content_sha256(
        observation(snr_samples=())
    )


def test_reordering_snr_samples_changes_the_digest() -> None:
    """A time series in another order is another measurement."""
    samples = EVIDENCE["snr_samples"]

    assert content_sha256(observation(snr_samples=samples)) != content_sha256(
        observation(snr_samples=tuple(reversed(samples)))
    )


def test_a_decode_block_keeps_its_missing_members_as_null() -> None:
    """Inside the new block, absence is rendered, so the block has one shape."""
    rendered = json.loads(
        canonical_bytes(observation(decode=DecodeStatistics("gr-satellites")))
    )

    assert rendered["decode"] == {
        "decoder": "gr-satellites",
        "decoder_version": None,
        "frames_decoded": None,
        "frames_failed": None,
    }


def test_a_non_finite_snr_sample_is_refused() -> None:
    """The same refusal `peak_snr_db` gets, for the same reason."""
    with pytest.raises(ValueError, match=r"Out of range|not JSON compliant"):
        canonical_bytes(
            observation(snr_samples=(SnrSample(DETECTED_AT, float("nan")),))
        )
