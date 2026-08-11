"""``meridian_client.credentials`` against a real temporary directory.

Files, not mocks. Everything this module is for happens at the filesystem
boundary — an interrupted write, a mode that is too permissive, a file that is
not there yet — and a mocked ``open`` proves none of it.

Marked as a unit test by living in ``tests/unit``: a temporary directory is not
infrastructure, and nothing here reaches a network or a database.

Reference: docs/DECISIONS.md D-023, D-034.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from meridian_client.credentials import (
    REGISTRATION_KEY_BYTES,
    StationCredentials,
    ensure_registration_key,
    load_credentials,
    save_credentials,
)

REGISTERED = StationCredentials(
    station_id="st_7fa3c1",
    bearer_token="a-bearer-token",
    registration_key="a-registration-key",
    heartbeat_interval_s=30,
)


def test_a_key_is_generated_on_first_call_and_reused_afterwards(
    tmp_path: Path,
) -> None:
    """The property D-023 rests on: the key is stable across restarts.

    A station that generated a fresh key on every boot could never recover a lost
    registration, because the platform stores the hash of the first one.
    """
    path = tmp_path / "registration_key"

    first = ensure_registration_key(path)
    second = ensure_registration_key(path)

    assert first == second
    assert path.read_text(encoding="utf-8").strip() == first


def test_two_stations_do_not_generate_the_same_key(tmp_path: Path) -> None:
    """The key is the only thing separating a retry from a different station.

    Base64 of 32 random bytes is about 43 characters, and the length is asserted
    because a key that silently became short would still round-trip, still pass
    every other test here, and quietly weaken the one proof D-023 rests on.
    """
    first = ensure_registration_key(tmp_path / "one" / "registration_key")
    second = ensure_registration_key(tmp_path / "two" / "registration_key")

    assert first != second
    minimum_length = REGISTRATION_KEY_BYTES  # base64 is longer than the raw bytes
    assert len(first) > minimum_length


def test_the_key_reaches_disk_before_it_is_returned(tmp_path: Path) -> None:
    """The ordering D-023 exists for, asserted rather than assumed.

    If the caller crashes between receiving this key and sending the register
    request, the retry has to present the same key. That only works if the write
    happened first — so the file must already be readable by the time the value
    is in the caller's hands.
    """
    path = tmp_path / "nested" / "registration_key"

    key = ensure_registration_key(path)

    assert path.exists()
    assert path.read_text(encoding="utf-8").strip() == key


def test_credentials_round_trip(tmp_path: Path) -> None:
    """What a station wrote before a reboot is what it reads after one."""
    path = tmp_path / "credentials.json"

    save_credentials(path, REGISTERED)

    assert load_credentials(path) == REGISTERED


def test_the_heartbeat_interval_survives_a_restart(tmp_path: Path) -> None:
    """MSP §4.2's response does not repeat the interval — only §4.1 states it.

    A station that dropped it would come back knowing who it is and not how often
    to say so.
    """
    path = tmp_path / "credentials.json"
    save_credentials(path, REGISTERED)

    reloaded = load_credentials(path)

    assert reloaded is not None
    assert reloaded.heartbeat_interval_s == 30


def test_an_unregistered_station_reads_none_rather_than_raising(
    tmp_path: Path,
) -> None:
    """First boot is the normal path through `load_credentials`, not a failure."""
    assert load_credentials(tmp_path / "never-written.json") is None


def test_a_corrupt_file_raises_instead_of_reading_as_unregistered(
    tmp_path: Path,
) -> None:
    """Absent and corrupt mean opposite things.

    Absent is a station that should register. Corrupt is a station that must not:
    registering again consumes a second invite and creates a duplicate row for
    one physical installation.
    """
    path = tmp_path / "credentials.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(ValueError, match="not a readable credential file"):
        load_credentials(path)


def test_a_file_missing_a_field_is_corrupt_rather_than_partly_loaded(
    tmp_path: Path,
) -> None:
    """A credential set with no token is not credentials."""
    path = tmp_path / "credentials.json"
    path.write_text(json.dumps({"station_id": "st_7fa3c1"}), encoding="utf-8")

    with pytest.raises(ValueError, match="not a readable credential file"):
        load_credentials(path)


def test_replacing_credentials_leaves_no_partial_file_behind(tmp_path: Path) -> None:
    """The temporary file is moved into place, not left beside the real one.

    A leftover `.partial` holding a superseded bearer token is a second copy of a
    secret that nothing will ever clean up.
    """
    path = tmp_path / "credentials.json"
    save_credentials(path, REGISTERED)

    rotated = StationCredentials(
        station_id=REGISTERED.station_id,
        bearer_token="a-rotated-token",
        registration_key=REGISTERED.registration_key,
        heartbeat_interval_s=30,
    )
    save_credentials(path, rotated)

    assert [one.name for one in sorted(tmp_path.iterdir())] == ["credentials.json"]
    reloaded = load_credentials(path)
    assert reloaded is not None and reloaded.bearer_token == "a-rotated-token"


@pytest.mark.skipif(os.name == "nt", reason="Windows honours only the read-only bit")
def test_secret_files_are_not_readable_by_other_users(tmp_path: Path) -> None:
    """A bearer token on a shared Pi is a credential other services can read."""
    credentials_path = tmp_path / "credentials.json"
    key_path = tmp_path / "registration_key"
    save_credentials(credentials_path, REGISTERED)
    ensure_registration_key(key_path)

    for path in (credentials_path, key_path):
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode & (stat.S_IRGRP | stat.S_IROTH | stat.S_IWGRP | stat.S_IWOTH) == 0
