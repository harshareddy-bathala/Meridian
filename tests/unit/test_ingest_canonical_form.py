"""The ingest renderer's rules, and that they still match the platform's.

There are now two canonical-JSON renderers in the repository.
:mod:`meridian.observations.canonical_body` is typed to ``NewObservation`` and
D-118 pins a golden digest over its exact output, so ingest could not reuse it
without putting every stored ``observations.content_sha256`` at the mercy of an
archive change; :mod:`meridian_ingest.raw_manifest` is written fresh to the same
rules.

Two renderers written to the same rules is a thing that rots quietly. The table
below is the check: each awkward value is rendered by ingest and looked for,
byte for byte, inside what the platform produced for an observation carrying it.
A digest taken in one module would otherwise come to mean something different
from a digest taken in the other, and nothing would say when that started.

Pure computation — no network, no database, no disk.

Reference: docs/DECISIONS.md D-070, D-118, D-141.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from meridian.observations.canonical_body import canonical_bytes as platform_bytes
from meridian.store.observations import NewObservation
from meridian_ingest.raw_manifest import (
    MalformedManifestError,
    canonical_bytes,
    canonical_sha256,
    instant,
)

STARTED_AT = datetime(2026, 8, 14, 9, 41, 18, tzinfo=UTC)
ENDED_AT = datetime(2026, 8, 14, 9, 52, 10, tzinfo=UTC)

INDIAN_STANDARD_TIME = timezone(timedelta(hours=5, minutes=30))
"""Where this project's station is. A real offset rather than a contrived one."""

AWKWARD_TEXT = [
    "",
    "plain",
    "naïve · σ · 1σ",
    'a "quoted" \\ backslash',
    "a\nnewline\tand\ttabs",
    "\u0000\u001f",
    "🛰 surrogate-pair territory",
    "ünïcode that NFC and NFD disagree about: é vs é",
]
"""Strings that serialisers disagree about: escaping, control characters,
non-ASCII, and two spellings of one accented letter."""

AWKWARD_NUMBERS = [0.0, -0.0, 1.5, 0.1 + 0.2, -273.15, 1e300, 5e-324]
"""Floats whose shortest repr is the thing a renderer could differ on, including
negative zero and the smallest subnormal."""

AWKWARD_INSTANTS = [
    STARTED_AT,
    STARTED_AT.astimezone(INDIAN_STANDARD_TIME),
    STARTED_AT.replace(microsecond=999_999),
    STARTED_AT.replace(microsecond=1),
    datetime(1969, 7, 20, 20, 17, 40, tzinfo=UTC),
]
"""Instants that separate a renderer's zone handling from its precision."""


def observation(**overrides: Any) -> NewObservation:
    """The smallest observation the platform renderer accepts."""
    fields: dict[str, Any] = {
        "assignment_id": "as_44b2",
        "station_id": "st_7fa3c1",
        "satellite_id": "norad:57166",
        "started_at": STARTED_AT,
        "ended_at": ENDED_AT,
        "outcome": "decoded",
        "signal_detected": True,
        "first_detection_at": None,
        "peak_snr_db": None,
        "doppler_samples": None,
        "products": (),
        "client_notes": None,
        "simulated": False,
    }
    fields.update(overrides)
    return NewObservation(**fields)


def rendered(value: object) -> bytes:
    """One value as the ingest renderer writes it, without its wrapper."""
    return canonical_bytes({"v": value}).removeprefix(b'{"v":').removesuffix(b"}")


@pytest.mark.parametrize("text", AWKWARD_TEXT)
def test_both_renderers_write_the_same_string(text: str) -> None:
    assert b'"client_notes":' + rendered(text) in platform_bytes(
        observation(client_notes=text)
    )


@pytest.mark.parametrize("number", AWKWARD_NUMBERS)
def test_both_renderers_write_the_same_number(number: float) -> None:
    assert b'"peak_snr_db":' + rendered(number) in platform_bytes(
        observation(peak_snr_db=number)
    )


@pytest.mark.parametrize("moment", AWKWARD_INSTANTS)
def test_both_renderers_write_the_same_instant(moment: datetime) -> None:
    """Offsets normalised to UTC, precision truncated to milliseconds, ``Z``."""
    assert b'"started_at":' + rendered(instant(moment)) in platform_bytes(
        observation(started_at=moment)
    )


@pytest.mark.parametrize("absent", [None, True, False])
def test_both_renderers_write_the_same_literal(absent: object) -> None:
    assert b'"signal_detected":' + rendered(absent) in platform_bytes(
        observation(signal_detected=absent)
    )


def test_both_renderers_refuse_a_value_json_has_no_literal_for() -> None:
    """``1e400`` parses to ``inf``, and a digest over it hashes what no column holds.

    Both refuse, and both refusals are ``ValueError`` — so a caller that already
    handles the platform's does not silently miss this one.
    """
    with pytest.raises(ValueError, match="not JSON compliant"):
        platform_bytes(observation(peak_snr_db=float("inf")))
    with pytest.raises(MalformedManifestError):
        canonical_bytes({"v": float("nan")})


def test_the_rendering_is_sorted_and_has_no_whitespace() -> None:
    """The two properties that make a byte string canonical at all."""
    text = canonical_bytes({"zulu": 1, "alpha": {"delta": 2, "bravo": 3}}).decode()

    assert " " not in text
    assert list(json.loads(text)) == ["alpha", "zulu"]
    assert list(json.loads(text)["alpha"]) == ["bravo", "delta"]


def test_array_order_is_left_alone() -> None:
    """An array here is a measurement; sorting one hides a scrambled artefact."""
    assert canonical_bytes({"v": [3, 1, 2]}) == b'{"v":[3,1,2]}'


def test_a_value_that_is_not_json_native_is_refused_rather_than_guessed() -> None:
    """A renderer that repairs its input produces bytes that depend on the caller.

    ``datetime`` is the one it would be most tempting to handle, and the one
    where a guess would silently pick a precision.
    """
    with pytest.raises(MalformedManifestError, match="not renderable"):
        canonical_bytes({"v": STARTED_AT})


def test_a_naive_instant_is_refused() -> None:
    with pytest.raises(MalformedManifestError, match="naive"):
        instant(datetime(2026, 8, 14, 9, 41, 18))  # noqa: DTZ001


def test_the_digest_is_taken_over_the_canonical_bytes() -> None:
    import hashlib

    payload = {"b": 2, "a": 1}

    assert canonical_sha256(payload) == hashlib.sha256(b'{"a":1,"b":2}').digest()
    assert len(canonical_sha256(payload)) == 32
