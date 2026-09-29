"""Peppered hashes, and verifying them across a pepper rotation.

A station's bearer token and registration key are stored as
``sha256(pepper ‖ secret)`` (D-017, D-023). Rotating the pepper used to strand
every station: its token stopped matching, and so did the registration key a
bound invite needs to recover it (D-034), so no station could be brought back
without re-registering under a new ``station_id``.

During a rotation the platform holds two peppers (D-201). The current one hashes
everything new and is tried first; the previous one is tried second, and only
to verify. A credential the previous pepper verifies is re-hashed under the
current one in the same request, so the overlap drains as stations call: every
live station has moved after one heartbeat interval, and a registration key
moves when it is next presented.

Reference: docs/DECISIONS.md D-017, D-023, D-034, D-201.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass

from meridian.store.station_tokens import (
    Connection,
    find_station_id_by_token_hash,
    rehash_registration_key,
    rehash_station_token,
)

__all__ = [
    "Peppers",
    "authenticate_across_rotation",
    "hash_with_pepper",
    "refresh_registration_key",
    "registration_key_matches",
]

_log = logging.getLogger(__name__)


def hash_with_pepper(pepper: str, secret: str) -> bytes:
    """``sha256(pepper ‖ secret)`` — the peppered hash D-017 and D-023 both use.

    Unlike an invite token (:func:`meridian.store.invites.hash_invite_token`),
    a station's bearer token and registration key are long-lived credentials,
    and the pepper is what keeps a read-only database leak from being enough
    on its own to confirm a guessed one.
    """
    return hashlib.sha256(pepper.encode("utf-8") + secret.encode("utf-8")).digest()


@dataclass(frozen=True, slots=True)
class Peppers:
    """The pepper in force, and the one being rotated away from, if any."""

    current: str
    previous: str = ""
    """Empty when no rotation is in progress."""

    def hash(self, secret: str) -> bytes:
        """The hash a credential is stored under from now on."""
        return hash_with_pepper(self.current, secret)

    def previous_hash(self, secret: str) -> bytes | None:
        """The hash under the previous pepper, or ``None`` outside a rotation."""
        if not self.previous:
            return None
        return hash_with_pepper(self.previous, secret)


def authenticate_across_rotation(
    conn: Connection, peppers: Peppers, bearer_token: str
) -> str | None:
    """The station a bearer token belongs to, moving it to the current pepper.

    The current hash is looked up first, so outside a rotation this is one
    indexed lookup, exactly as before D-201. The previous hash costs a second
    lookup only for a token the current pepper does not know.
    """
    current_hash = peppers.hash(bearer_token)
    station_id = find_station_id_by_token_hash(conn, current_hash)
    previous_hash = peppers.previous_hash(bearer_token)
    if station_id is not None or previous_hash is None:
        return station_id
    station_id = find_station_id_by_token_hash(conn, previous_hash)
    if station_id is None:
        return None
    rehash_station_token(
        conn,
        station_id=station_id,
        old_token_sha256=previous_hash,
        new_token_sha256=current_hash,
    )
    _log.info("station %s: bearer token re-hashed under the new pepper", station_id)
    return station_id


def registration_key_matches(
    peppers: Peppers, stored_sha256: bytes, registration_key: str
) -> bool:
    """Whether ``registration_key`` hashes to ``stored_sha256`` under either pepper.

    ``compare_digest``, not ``==``: this key authorises minting a new bearer
    token on an existing station (D-023, D-034), so a timing oracle on it is a
    credential-recovery path, not merely an information leak (D-017).
    """
    candidates = [peppers.hash(registration_key)]
    previous = peppers.previous_hash(registration_key)
    if previous is not None:
        candidates.append(previous)
    # Every candidate is compared, so the time taken does not say which matched.
    matches = [hmac.compare_digest(c, stored_sha256) for c in candidates]
    return any(matches)


def refresh_registration_key(
    conn: Connection,
    peppers: Peppers,
    *,
    station_id: str,
    stored_sha256: bytes,
    registration_key: str,
) -> None:
    """Store a verified registration key under the current pepper, if it is not.

    Called only after :func:`registration_key_matches` has accepted the key, so
    the plaintext in hand is known to be the station's own.
    """
    current_hash = peppers.hash(registration_key)
    if hmac.compare_digest(current_hash, stored_sha256):
        return
    rehash_registration_key(
        conn,
        station_id=station_id,
        old_sha256=stored_sha256,
        new_sha256=current_hash,
    )
    _log.info("station %s: registration key re-hashed under the new pepper", station_id)
