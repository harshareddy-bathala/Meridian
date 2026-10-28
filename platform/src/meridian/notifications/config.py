"""Which stations get reports, on which channel, and how delivery retries.

A TOML file, named by ``MERIDIAN_NOTIFICATIONS_CONFIG``; ``deploy/notifications.toml.
example`` is the reference. **It holds no address** (D-300): it says that a
station's reports go by email, Telegram or to the sink, and the address or
chat id for each lives in the sender's secret file alone
(:mod:`meridian.notifications.recipients`). So the jobs process, which queues
reports, never holds a recipient, and neither does this file's history.

Only stations the team operates are listed (D-107 option b). **A simulated
station may be listed for the sink only**: :func:`check_stations` refuses the
file against the registry before any report is queued, as the database's own
check would at the first insert (D-301).

No file means no reports: the task still runs, finds no station, and queues
nothing.

Standard library only.

Reference: docs/DECISIONS.md D-107, D-300, D-301, D-305.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from meridian.store.stations import Connection

__all__ = [
    "CHANNELS",
    "NOTIFICATIONS_CONFIG_ENV",
    "NotificationsConfig",
    "NotificationsConfigError",
    "Recipient",
    "RetryPolicy",
    "check_stations",
    "load_deployed_notifications_config",
    "load_notifications_config",
]

NOTIFICATIONS_CONFIG_ENV = "MERIDIAN_NOTIFICATIONS_CONFIG"
CHANNELS = ("email", "telegram", "sink")
HOUR_S = 3_600


class NotificationsConfigError(ValueError):
    """The notifications file cannot be read, or holds a bad value."""


@dataclass(frozen=True, slots=True)
class Recipient:
    """One station that gets reports, and the channel they go by."""

    station_id: str
    channel: str
    weekly: bool = True


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """How a failed delivery is tried again (D-305)."""

    first_s: int = 60
    """The wait after the first failure; each failure after doubles it."""

    cap_s: int = 6 * HOUR_S
    attempts: int = 8
    """Attempts before a report is abandoned."""

    timeout_s: int = 10
    """The most one attempt may take."""

    def wait_after(self, failures: int) -> int:
        """Seconds to wait after the ``failures``-th failure, from one."""
        return min(self.cap_s, self.first_s * (1 << (failures - 1)))


@dataclass(frozen=True, slots=True)
class NotificationsConfig:
    """The deployment's owner reports."""

    stations: tuple[Recipient, ...] = ()
    wait_s: int = 6 * HOUR_S
    """How long after its window a pass is reported with whatever is stored."""

    weekly_settle_s: int = 6 * HOUR_S
    """How long after a week ends its report is queued."""

    sink_directory: str = "/var/lib/meridian/report-sink"
    retry: RetryPolicy = field(default_factory=RetryPolicy)

    def channels(self, *, weekly: bool = False) -> dict[str, str]:
        """Each station's channel, by station id.

        With ``weekly``, only the stations that get a weekly report.
        """
        return {
            one.station_id: one.channel
            for one in self.stations
            if one.weekly or not weekly
        }


_TABLES = {"reports", "retry", "sink", "station"}


def load_notifications_config(path: Path | None) -> NotificationsConfig:
    """Read the file at ``path``, or no reports at all when ``path`` is ``None``.

    Raises:
        NotificationsConfigError: The file cannot be read, or is refused.
    """
    if path is None:
        return NotificationsConfig()
    try:
        parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise NotificationsConfigError(f"{path}: {exc}") from exc
    unknown = set(parsed) - _TABLES
    if unknown:
        raise NotificationsConfigError(f"{path}: unknown tables {sorted(unknown)}")
    reports = _table(parsed, "reports", {"wait_s", "weekly_settle_s"})
    retry = _table(parsed, "retry", {"first_s", "cap_s", "attempts", "timeout_s"})
    sink = _table(parsed, "sink", {"directory"})
    defaults = NotificationsConfig()
    base = RetryPolicy()
    policy = RetryPolicy(
        first_s=_whole(retry, "first_s", base.first_s, 1, HOUR_S),
        cap_s=_whole(retry, "cap_s", base.cap_s, 1, 24 * HOUR_S),
        attempts=_whole(retry, "attempts", base.attempts, 1, 50),
        timeout_s=_whole(retry, "timeout_s", base.timeout_s, 1, 120),
    )
    if policy.cap_s < policy.first_s:
        raise NotificationsConfigError("retry.cap_s must be at least retry.first_s")
    directory = sink.get("directory", defaults.sink_directory)
    if not isinstance(directory, str) or not directory.strip():
        raise NotificationsConfigError("sink.directory must be a path")
    return NotificationsConfig(
        stations=_stations(parsed.get("station", [])),
        wait_s=_whole(reports, "wait_s", defaults.wait_s, 0, 7 * 24 * HOUR_S),
        weekly_settle_s=_whole(
            reports, "weekly_settle_s", defaults.weekly_settle_s, 0, 3 * 24 * HOUR_S
        ),
        sink_directory=directory,
        retry=policy,
    )


def load_deployed_notifications_config() -> NotificationsConfig:
    """The file :data:`NOTIFICATIONS_CONFIG_ENV` names, or no reports.

    Raises:
        NotificationsConfigError: The file named cannot be read, or is refused.
    """
    named = os.environ.get(NOTIFICATIONS_CONFIG_ENV, "").strip()
    return load_notifications_config(Path(named) if named else None)


def check_stations(conn: Connection, config: NotificationsConfig) -> None:
    """Refuse the file against the registry.

    A station it names must be registered, and a simulated one's reports go to
    the sink only (rule 5).

    Raises:
        NotificationsConfigError: The registry disagrees with the file.
    """
    if not config.stations:
        return
    with conn.cursor() as cur:
        cur.execute(
            "select station_id, simulated from stations where station_id = any (%s)",
            ([one.station_id for one in config.stations],),
        )
        simulated = {str(row[0]): bool(row[1]) for row in cur.fetchall()}
    for one in config.stations:
        if one.station_id not in simulated:
            raise NotificationsConfigError(f"no station {one.station_id} is registered")
        if simulated[one.station_id] and one.channel != "sink":
            raise NotificationsConfigError(
                f"{one.station_id} is simulated, so its reports go to the sink only,"
                f" not by {one.channel}"
            )


def _stations(raw: object) -> tuple[Recipient, ...]:
    if not isinstance(raw, list):
        raise NotificationsConfigError("[[station]] must be an array of tables")
    found: list[Recipient] = []
    for entry in raw:
        one = _recipient(entry)
        if any(held.station_id == one.station_id for held in found):
            raise NotificationsConfigError(f"{one.station_id} is listed twice")
        found.append(one)
    return tuple(found)


def _recipient(entry: object) -> Recipient:
    if not isinstance(entry, dict):
        raise NotificationsConfigError("each [[station]] must be a table")
    unknown = set(entry) - {"station_id", "channel", "weekly"}
    if unknown:
        raise NotificationsConfigError(f"[[station]] unknown keys {sorted(unknown)}")
    station, channel = entry.get("station_id"), entry.get("channel")
    weekly = entry.get("weekly", True)
    if not isinstance(station, str) or not station.strip():
        raise NotificationsConfigError("[[station]] needs a station_id")
    if not isinstance(channel, str) or channel not in CHANNELS:
        raise NotificationsConfigError(
            f"{station}: channel must be one of {', '.join(CHANNELS)}, not {channel!r}"
        )
    if not isinstance(weekly, bool):
        raise NotificationsConfigError(f"{station}: weekly must be true or false")
    return Recipient(station_id=station, channel=channel, weekly=weekly)


def _table(parsed: dict[str, object], name: str, keys: set[str]) -> dict[str, object]:
    table = parsed.get(name, {})
    if not isinstance(table, dict):
        raise NotificationsConfigError(f"[{name}] must be a table")
    unknown = set(table) - keys
    if unknown:
        raise NotificationsConfigError(f"[{name}] unknown keys {sorted(unknown)}")
    return {str(key): value for key, value in table.items()}


def _whole(
    table: dict[str, object], key: str, default: int, low: int, high: int
) -> int:
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise NotificationsConfigError(f"{key} must be a whole number, not {value!r}")
    if not low <= value <= high:
        raise NotificationsConfigError(f"{key} must be in {low}..{high}, not {value}")
    return value
