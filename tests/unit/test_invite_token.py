"""Pure functions in ``meridian.store.invites`` — no database needed.

Everything else in that module talks to Postgres and is covered by
``tests/integration/test_store_invites.py``; these two functions don't, and
CLAUDE.local.md §3 asks that computation be separated from I/O for exactly
this reason — so it can be tested without one.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from meridian.store.invites import expiry_from_days, generate_invite_token


def test_generated_token_hash_matches_its_plaintext() -> None:
    plaintext, token_sha256 = generate_invite_token()
    assert hashlib.sha256(plaintext.encode("ascii")).digest() == token_sha256


def test_generated_tokens_do_not_collide() -> None:
    """32 random bytes each; a collision here would mean a broken RNG,
    not bad luck."""
    first, _ = generate_invite_token()
    second, _ = generate_invite_token()
    assert first != second


def test_expiry_from_days_is_none_when_no_expiry_was_requested() -> None:
    assert expiry_from_days(None, now=datetime.now(UTC)) is None


def test_expiry_from_days_adds_whole_days() -> None:
    now = datetime(2026, 8, 14, 9, 0, 0, tzinfo=UTC)
    assert expiry_from_days(7, now=now) == now + timedelta(days=7)
