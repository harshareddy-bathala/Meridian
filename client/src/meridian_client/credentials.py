"""The station's identity on disk, and the key that can recover it.

A station holds two secrets. The **registration key** it generates itself, once,
before it ever contacts the platform; the **bearer token** the platform mints and
returns exactly once, in the registration response. This module writes both down
in a way that survives a power cut mid-write, and reads them back on the next
boot so a restarted station rejoins as itself rather than as a second station.

The two have different lifetimes, so they live in different files. A bearer token
can be rotated — an operator issues a replacement invite bound to the station,
and the station presents it together with the key it has always had (D-034). The
key outlives every token it ever recovers, which a single combined file would
obscure the moment a rotation rewrote it.

Reference: docs/DECISIONS.md D-023 (the registration key), D-024 (a 401 is not a
re-registration), D-034 (rotation binds to the station).
"""

from __future__ import annotations

import json
import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "REGISTRATION_KEY_BYTES",
    "StationCredentials",
    "ensure_registration_key",
    "load_credentials",
    "save_credentials",
]

REGISTRATION_KEY_BYTES = 32
"""Entropy in the registration key, from D-023.

Thirty-two bytes because the key is the sole proof that a retried registration
comes from the same station as the first attempt. ``secrets.token_urlsafe``
renders it as roughly 43 URL-safe characters, which travel in a JSON body
unescaped.
"""

SECRET_FILE_MODE = stat.S_IRUSR | stat.S_IWUSR
"""``0600`` — readable and writable by the owning user, nobody else.

Set on POSIX, where a station client typically shares a Raspberry Pi with other
service accounts. Windows ignores everything but the read-only bit, so the mode
is a best effort there rather than a guarantee, and the file's directory is what
actually protects it.
"""


@dataclass(frozen=True, slots=True)
class StationCredentials:
    """What a registered station needs in order to speak MSP again after a boot.

    ``registration_key`` is carried here as well as in its own file because
    every use of the credentials that could need it — a rotation after a 401 —
    needs both halves together, and reading two files at every call site is how
    one of them eventually gets forgotten.
    """

    station_id: str
    bearer_token: str
    registration_key: str

    heartbeat_interval_s: int
    """The cadence the platform asked for, from MSP §4.1's registration response.

    Persisted because **§4.2's heartbeat response does not repeat it** — only
    registration states it. A station that did not store it would come back from
    a restart knowing its identity and not knowing how often to use it.
    """


def _write_secret_file(path: Path, text: str) -> None:
    """Replace ``path`` with ``text``, atomically, and restrict its mode.

    Written to a sibling temporary file and moved into place, so a power cut
    leaves either the old contents or the new and never a half-written file. A
    truncated credential file is worse than an absent one: absent is a station
    that has not registered, and truncated is a station that cannot register and
    cannot explain why.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".partial")
    temporary.write_text(text, encoding="utf-8")
    os.chmod(temporary, SECRET_FILE_MODE)  # noqa: PTH101 — Path has no chmod on 3.11
    os.replace(temporary, path)  # noqa: PTH105 — Path.replace is the same call


def ensure_registration_key(path: Path) -> str:
    """The station's registration key, generating and storing one on first call.

    Args:
        path: Where the key is kept. Created, with its parent directories, if it
            does not exist.

    Returns:
        The key, as the platform expects it in ``register``.

    Note:
        **Load and create are one function on purpose**, against the usual rule
        that a name containing "or" is two functions. D-023's whole mechanism is
        that the key reaches disk *before* the registration request is sent — a
        token lost in transit is recoverable only if the key identifying the
        retry survived the crash that lost it. Split into ``load`` and
        ``create``, the ordering becomes something every caller has to get right,
        and the one caller that got it wrong would leave a station holding a
        consumed invite and no credential, which is exactly the failure D-023
        exists to prevent.
    """
    if path.exists():
        return path.read_text(encoding="utf-8").strip()

    key = secrets.token_urlsafe(REGISTRATION_KEY_BYTES)
    _write_secret_file(path, key)
    return key


def save_credentials(path: Path, credentials: StationCredentials) -> None:
    """Write a registered station's identity to ``path``, replacing any previous.

    Args:
        path: The credential file. Created, with its parent directories, if it
            does not exist.
        credentials: What the registration response returned.
    """
    _write_secret_file(
        path,
        json.dumps(
            {
                "station_id": credentials.station_id,
                "bearer_token": credentials.bearer_token,
                "registration_key": credentials.registration_key,
                "heartbeat_interval_s": credentials.heartbeat_interval_s,
            },
            indent=2,
        )
        + "\n",
    )


def load_credentials(path: Path) -> StationCredentials | None:
    """Read a station's identity back, or ``None`` if it has not registered yet.

    Args:
        path: The credential file written by :func:`save_credentials`.

    Returns:
        The stored credentials, or ``None`` when the file does not exist.

    Raises:
        ValueError: The file exists but is not the object this module writes.
            Raised rather than returning ``None``, because the two mean opposite
            things: absent is a station that should register, and corrupt is a
            station that must not, since registering again would consume a second
            invite and create a duplicate row for one physical installation.

    Note:
        ``None`` for a missing file rather than an exception — a first boot is
        the normal path through this function, not a failure.
    """
    if not path.exists():
        return None

    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
        return StationCredentials(
            station_id=str(stored["station_id"]),
            bearer_token=str(stored["bearer_token"]),
            registration_key=str(stored["registration_key"]),
            heartbeat_interval_s=int(stored["heartbeat_interval_s"]),
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{path} is not a readable credential file: {exc}") from exc
