"""Pure functions in ``meridian.registry.psycopg_registry`` — no database needed.

Everything else in that module talks to Postgres and is covered by
``tests/integration/test_psycopg_registry.py``.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from meridian.registry.psycopg_registry import (
    generate_bearer_token,
    generate_station_id,
    hash_with_pepper,
    is_recovery_eligible,
)


def test_hash_with_pepper_matches_the_documented_construction() -> None:
    """``sha256(pepper ‖ secret)`` — the exact concatenation D-017 and D-023 name."""
    expected = hashlib.sha256(b"pepper123secret456").digest()
    assert hash_with_pepper("pepper123", "secret456") == expected


def test_hash_with_pepper_is_sensitive_to_the_pepper() -> None:
    assert hash_with_pepper("a", "secret") != hash_with_pepper("b", "secret")


def test_generate_station_id_matches_the_st_prefix_shape() -> None:
    """``st_`` plus 6 hex characters — the ``st_7fa3c1`` shape MSP-SPEC.md uses."""
    station_id = generate_station_id()
    assert station_id.startswith("st_")
    assert len(station_id) == len("st_") + 6
    int(station_id.removeprefix("st_"), 16)  # raises ValueError if not hex


def test_generate_station_id_does_not_collide() -> None:
    assert generate_station_id() != generate_station_id()


def test_generate_bearer_token_hash_matches_its_plaintext() -> None:
    plaintext, token_hash = generate_bearer_token("pepper")
    assert hash_with_pepper("pepper", plaintext) == token_hash


def test_generate_bearer_token_does_not_collide() -> None:
    first, _ = generate_bearer_token("pepper")
    second, _ = generate_bearer_token("pepper")
    assert first != second


BASE = datetime(2026, 8, 14, 9, 0, 0, tzinfo=UTC)


def test_recovery_eligible_with_no_heartbeat_and_inside_the_window() -> None:
    assert is_recovery_eligible(
        last_heartbeat_at=None,
        registered_at=BASE,
        now=BASE + timedelta(minutes=30),
        window_s=3600,
    )


def test_recovery_ineligible_once_a_heartbeat_has_arrived() -> None:
    """A station that has heartbeat holds a working token by definition (D-023) —
    ineligible regardless of how far inside the window the request arrives."""
    assert not is_recovery_eligible(
        last_heartbeat_at=BASE + timedelta(seconds=1),
        registered_at=BASE,
        now=BASE + timedelta(minutes=1),
        window_s=3600,
    )


def test_recovery_ineligible_past_the_window() -> None:
    assert not is_recovery_eligible(
        last_heartbeat_at=None,
        registered_at=BASE,
        now=BASE + timedelta(seconds=3601),
        window_s=3600,
    )


def test_recovery_eligible_at_exactly_the_window_boundary() -> None:
    """``<=``, not ``<`` — the instant the window closes on is still eligible."""
    assert is_recovery_eligible(
        last_heartbeat_at=None,
        registered_at=BASE,
        now=BASE + timedelta(seconds=3600),
        window_s=3600,
    )
