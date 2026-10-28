"""Who each station's reports go to: the sender's secret, and nowhere else.

A TOML file named by ``MERIDIAN_RECIPIENTS_FILE``, mounted read-only into the
``notifications`` service alone from ``deploy/secrets/recipients.toml``,
which is gitignored. One key per station, its value the email address or
Telegram chat id that station's reports go to::

    [recipients]
    st_0000000000000000 = "owner@example.org"
    team = "123456789"

**An address is personal data** (D-107), so it is held here and in no other
place: not in the database, not in a log line, not in an error, not in any MSP
message (D-300). Every message this module raises names a station, never what
the station maps to. A key need not be a station: ``meridian notify test``
sends one fixed message to any key, which is how a channel is checked before a
station exists.

Standard library only.

Reference: docs/DECISIONS.md D-107, D-300.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from meridian.notifications.config import NotificationsConfigError

__all__ = ["RECIPIENTS_FILE_ENV", "load_deployed_recipients", "load_recipients"]

RECIPIENTS_FILE_ENV = "MERIDIAN_RECIPIENTS_FILE"
MAX_RECIPIENT = 254
"""The longest address SMTP carries; a chat id is far shorter."""


def load_recipients(path: Path | None) -> dict[str, str]:
    """Each key's recipient, or none at all when ``path`` is ``None``.

    Raises:
        NotificationsConfigError: The file cannot be read, or is refused. The
            message never contains a recipient.
    """
    if path is None:
        return {}
    try:
        parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise NotificationsConfigError(
            f"{path} cannot be read: {exc.strerror}"
        ) from None
    except tomllib.TOMLDecodeError:
        # The parser's message quotes the offending line, which is an address.
        raise NotificationsConfigError(f"{path} is not TOML") from None
    if set(parsed) - {"recipients"}:
        raise NotificationsConfigError(f"{path}: only a [recipients] table is read")
    table = parsed.get("recipients", {})
    if not isinstance(table, dict):
        raise NotificationsConfigError(f"{path}: [recipients] must be a table")
    return {str(key): _recipient(path, str(key), value) for key, value in table.items()}


def _recipient(path: Path, key: str, value: object) -> str:
    """One recipient, checked; a refusal names the key and never the value."""
    if not isinstance(value, str) or not value.strip():
        raise NotificationsConfigError(f"{path}: {key}'s recipient must be text")
    if len(value) > MAX_RECIPIENT or any(c.isspace() for c in value.strip()):
        raise NotificationsConfigError(f"{path}: {key}'s recipient is malformed")
    return value.strip()


def load_deployed_recipients() -> dict[str, str]:
    """The file :data:`RECIPIENTS_FILE_ENV` names, or no recipients.

    Raises:
        NotificationsConfigError: The file named cannot be read, or is refused.
    """
    named = os.environ.get(RECIPIENTS_FILE_ENV, "").strip()
    return load_recipients(Path(named) if named else None)
