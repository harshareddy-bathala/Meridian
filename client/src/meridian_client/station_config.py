"""One station's configuration, read from a TOML file.

A station has to know where its platform and its state directory are, what it
receives with, what decodes each mode, and the two judgements D-122's table
cannot measure. This module reads all of that from one file with ``tomllib`` —
the standard library's, so the client still installs on a Pi with no parser
behind it — and returns the values the reception layer already takes.

**Refused rather than defaulted.** An unknown table or key is a configuration
error naming what was not recognised, exactly as an unknown decoder placeholder
is (D-124). A station whose typo left a setting at its default would find out at
the end of a pass, and a pass never repeats.

**Whether the station is simulated is not in this file.** It is written into the
credentials at registration and read back from there. An operator who could set
it here could point a simulated receiver at a station the platform records as
measuring the real sky, which is the case D-125 exists to refuse (D-127).

Reference: docs/DECISIONS.md D-120 to D-127; docs/OPERATIONS.md § Station
reception.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping, Set
from dataclasses import dataclass, field
from pathlib import Path

from meridian_client.reception.disk_guard import DiskGuard
from meridian_client.reception.outcome_rules import OutcomePolicy
from meridian_client.reception.subprocess_decoder import DecoderCommand
from meridian_client.reception.synthetic_receivers import RecordingSource

__all__ = [
    "RECEIVER_KINDS",
    "ConfigError",
    "ReceiverConfig",
    "StationConfig",
    "StationPaths",
    "load_station_config",
]

RECEIVER_KINDS = ("simulated", "replay")
"""What a station can receive with today.

No physical SDR adapter ships yet (D-124), and naming one here would promise
something that does not exist. A station configured for one is told so.
"""

TABLES = frozenset({"station", "receiver", "decoders", "policy", "disk", "retention"})
RECORDING_KEYS = frozenset(
    {"path", "sample_rate_hz", "sample_format", "centre_freq_hz", "gain_db"}
)


class ConfigError(ValueError):
    """A configuration file a station will not start from."""


@dataclass(frozen=True, slots=True)
class StationPaths:
    """Where one station keeps everything it owns."""

    state_dir: Path
    credentials: Path
    registration_key: Path
    held_assignments: Path
    outbox: Path
    captures: Path

    @classmethod
    def under(cls, state_dir: Path) -> StationPaths:
        """The standard layout below ``state_dir``."""
        return cls(
            state_dir=state_dir,
            credentials=state_dir / "credentials.json",
            registration_key=state_dir / "registration_key",
            held_assignments=state_dir / "held.json",
            outbox=state_dir / "outbox",
            captures=state_dir / "captures",
        )


@dataclass(frozen=True, slots=True)
class ReceiverConfig:
    """What the station receives with, and what that receiver needs."""

    kind: str
    sample_rate_hz: int = 1_000
    """The simulated receiver's rate. The replay receiver takes each recording's
    own instead."""

    recordings: Mapping[str, RecordingSource] = field(default_factory=dict)
    """For ``replay``: which recording stands in for which assignment."""


@dataclass(frozen=True, slots=True)
class StationConfig:
    """Everything one station is configured with."""

    base_url: str
    paths: StationPaths
    receiver: ReceiverConfig
    decoders: Mapping[str, DecoderCommand]
    policy: OutcomePolicy
    disk: DiskGuard
    keep_recordings: bool


def load_station_config(path: Path) -> StationConfig:
    """Read and check one station's configuration file.

    Args:
        path: The TOML file. Relative paths inside it — the state directory, a
            replayed recording — resolve against its own directory, so a
            configuration and the files it names travel together.

    Returns:
        The configuration, with every decoder command already validated.

    Raises:
        ConfigError: The file is missing, is not TOML, names something this
            client does not recognise, or holds a value it cannot use.
    """
    try:
        stored = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"{path} could not be read: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from exc

    _refuse_unknown(set(stored), TABLES, "table")
    beside = path.parent
    station = _table(stored, "station", {"base_url", "state_dir"})
    receiver = _table(stored, "receiver", {"kind", "sample_rate_hz", "recordings"})
    return StationConfig(
        base_url=_text(station, "base_url", default="http://localhost:8000"),
        paths=StationPaths.under(
            _path(station, "state_dir", beside, default=beside / "state")
        ),
        receiver=_receiver(receiver, beside),
        decoders=_decoders(_table(stored, "decoders", None)),
        policy=_policy(
            _table(stored, "policy", {"snr_threshold_db", "minimum_coverage"})
        ),
        disk=_disk(
            _table(stored, "disk", {"bytes_per_second", "margin", "reserve_bytes"})
        ),
        keep_recordings=_flag(
            _table(stored, "retention", {"keep_recordings"}), "keep_recordings"
        ),
    )


def _receiver(stored: Mapping[str, object], beside: Path) -> ReceiverConfig:
    """The ``[receiver]`` table: what this station listens with."""
    kind = _text(stored, "kind", default="simulated")
    if kind not in RECEIVER_KINDS:
        known = ", ".join(RECEIVER_KINDS)
        raise ConfigError(
            f"receiver.kind must be one of {known}, not {kind!r}; no physical "
            "receiver adapter ships yet (D-124)"
        )
    named = _sub_table(stored.get("recordings", {}), "receiver.recordings")
    recordings = {
        assignment_id: _recording(
            _sub_table(one, f"receiver.recordings.{assignment_id}"), beside
        )
        for assignment_id, one in named.items()
    }
    if kind == "replay" and not recordings:
        raise ConfigError("receiver.kind is replay, but no recordings are named")
    return ReceiverConfig(
        kind=kind,
        sample_rate_hz=_whole(stored, "sample_rate_hz", default=1_000),
        recordings=recordings,
    )


def _recording(stored: Mapping[str, object], beside: Path) -> RecordingSource:
    """One entry of ``[receiver.recordings]``: a file that stands in for a pass."""
    _refuse_unknown(set(stored), RECORDING_KEYS, "receiver.recordings key")
    try:
        return RecordingSource(
            path=_path(stored, "path", beside, default=None),
            sample_rate_hz=_whole(stored, "sample_rate_hz", default=None),
            sample_format=_text(stored, "sample_format", default="cf32"),
            centre_freq_hz=_whole(stored, "centre_freq_hz", default=None),
            gain_db=_optional_number(stored, "gain_db"),
        )
    except ValueError as exc:
        raise ConfigError(f"receiver.recordings: {exc}") from exc


def _decoders(stored: Mapping[str, object]) -> dict[str, DecoderCommand]:
    """The ``[decoders.<mode>]`` tables, each validated as it is read (D-124)."""
    commands = {}
    for mode, one in stored.items():
        table = _sub_table(one, f"decoders.{mode}")
        _refuse_unknown(set(table), {"argv", "timeout_s"}, f"decoders.{mode} key")
        try:
            commands[mode] = DecoderCommand(
                argv=tuple(_argv(table, mode)),
                timeout_s=_number(table, "timeout_s", default=900.0),
            )
        except ValueError as exc:
            raise ConfigError(f"decoders.{mode}: {exc}") from exc
    return commands


def _argv(stored: Mapping[str, object], mode: str) -> list[str]:
    """One decoder's argv: the program, then its arguments, as written."""
    value = stored.get("argv")
    if not isinstance(value, list) or not all(isinstance(one, str) for one in value):
        raise ConfigError(f"decoders.{mode}.argv must be an array of strings")
    return [str(one) for one in value]


def _policy(stored: Mapping[str, object]) -> OutcomePolicy:
    """The ``[policy]`` table: the judgements D-122's table cannot measure."""
    try:
        return OutcomePolicy(
            snr_threshold_db=_number(stored, "snr_threshold_db", default=3.0),
            minimum_coverage=_number(stored, "minimum_coverage", default=0.8),
        )
    except ValueError as exc:
        raise ConfigError(f"policy: {exc}") from exc


def _disk(stored: Mapping[str, object]) -> DiskGuard:
    """The ``[disk]`` table: how much room a capture needs before it starts."""
    try:
        return DiskGuard(
            bytes_per_second=_whole(stored, "bytes_per_second", default=2_048_000),
            margin=_number(stored, "margin", default=1.25),
            reserve_bytes=_whole(stored, "reserve_bytes", default=1024**3),
        )
    except ValueError as exc:
        raise ConfigError(f"disk: {exc}") from exc


def _refuse_unknown(found: Set[str], known: Set[str], what: str) -> None:
    """Refuse anything this client would otherwise ignore."""
    unknown = sorted(found - known)
    if unknown:
        raise ConfigError(f"unknown {what}: {', '.join(unknown)}")


def _table(
    stored: Mapping[str, object], name: str, keys: Set[str] | None
) -> Mapping[str, object]:
    """One top-level table, empty when absent, with its keys checked."""
    table = _sub_table(stored.get(name, {}), name)
    if keys is not None:
        _refuse_unknown(set(table), keys, f"{name} key")
    return table


def _sub_table(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ConfigError(f"{name} must be a table, not {value!r}")
    return value


def _text(stored: Mapping[str, object], key: str, *, default: str | None) -> str:
    value = stored.get(key, default)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{key} must be a non-empty string, not {value!r}")
    return value


def _path(
    stored: Mapping[str, object], key: str, beside: Path, *, default: Path | None
) -> Path:
    """A path, resolved against the configuration file's own directory."""
    if stored.get(key) is None and default is not None:
        return default
    return beside / Path(_text(stored, key, default=None))


def _whole(stored: Mapping[str, object], key: str, *, default: int | None) -> int:
    value = stored.get(key, default)
    # bool is a subclass of int, and `true` is not a sample rate.
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(f"{key} must be a positive whole number, not {value!r}")
    return value


def _number(stored: Mapping[str, object], key: str, *, default: float) -> float:
    value = stored.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigError(f"{key} must be a number, not {value!r}")
    return float(value)


def _optional_number(stored: Mapping[str, object], key: str) -> float | None:
    return None if stored.get(key) is None else _number(stored, key, default=0.0)


def _flag(stored: Mapping[str, object], key: str) -> bool:
    value = stored.get(key, False)
    if not isinstance(value, bool):
        raise ConfigError(f"{key} must be true or false, not {value!r}")
    return value
