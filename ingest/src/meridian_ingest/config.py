"""One TOML file: where the raw store lives, and what each source is allowed.

``meridian-ingest`` runs on whatever machine holds the archive snapshot, not in
the compose stack, so its settings are a file an analyst edits rather than the
environment the platform reads (``meridian.config``). A per-source table is the
shape the settings actually have, and expressing a nested table as environment
variables is how a configuration becomes undocumentable.

**No credential ever appears in this file.** A source that needs a key names the
*environment variable* that holds one, and
:func:`meridian_ingest.credentials.read_api_key` reads it when a fetch is about
to happen — never onto a settings object, where a traceback or a log line would
print it. Pasting the key itself into ``api_key_env`` is refused here, by name,
because that is the mistake the arrangement exists to catch and it is the kind
that gets committed.

**Strict.** An unknown key is refused rather than ignored: a misspelled
``request_budget`` that silently kept the default is a limit somebody believes
they set. A source id that nothing is registered under is refused the same way,
naming the ids that do exist.

**Optional.** With no file at all the defaults fetch the reference archive into
``data/ingest/raw``, so the completion gate runs for somebody who has just
installed the distribution and configured nothing.

Reference: docs/DECISIONS.md D-114, D-134, D-141.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from meridian_ingest.adapters import REGISTRY
from meridian_ingest.credentials import ENV_NAME
from meridian_ingest.politeness import RetryPolicy

__all__ = [
    "CONFIG_ENV",
    "DEFAULT_CONFIG_NAME",
    "DEFAULT_RAW_ROOT",
    "ConfigurationError",
    "IngestSettings",
    "SourceSettings",
    "find_config",
    "load_settings",
]

CONFIG_ENV = "MERIDIAN_INGEST_CONFIG"
DEFAULT_CONFIG_NAME = "ingest.toml"
DEFAULT_RAW_ROOT = Path("data/ingest/raw")
"""``data/`` is already gitignored, so a snapshot cannot be committed by
accident — which matters more here than anywhere else, because these bytes
belong to somebody else."""

DEFAULT_REQUEST_BUDGET = 50
DEFAULT_TIMEOUT_S = 30.0

_TOP_LEVEL = frozenset({"raw_root", "timeout_s", "contact", "retry", "sources"})
_RETRY_KEYS = frozenset({"attempts", "base_delay_s", "max_delay_s"})
_SOURCE_KEYS = frozenset({"enabled", "base_url", "request_budget", "api_key_env"})


class ConfigurationError(ValueError):
    """A settings file that cannot be obeyed as written.

    Every message names the key and says what was expected, because the person
    reading it is holding the file open and a refusal that does not say which
    line is a refusal they have to bisect.
    """


@dataclass(frozen=True, slots=True)
class SourceSettings:
    """What one source is allowed, and where its key comes from."""

    source_id: str
    enabled: bool = True
    """False takes a source out of ``fetch`` without deleting its table, so the
    terms it was registered under stay written down."""

    base_url: str | None = None
    """Overrides the adapter's own root, for a mirror or a staging host. https
    only, for the reason every URL here is."""

    request_budget: int = DEFAULT_REQUEST_BUDGET
    """The ceiling for one fetch of this source, retries included."""

    api_key_env: str | None = None
    """The **name** of an environment variable, never a key.

    Read through :func:`read_api_key` at the moment it is needed, so it is
    never a field on a settings object that something might print.
    """


@dataclass(frozen=True, slots=True)
class IngestSettings:
    """Everything ``meridian-ingest`` needs to run."""

    raw_root: Path
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    timeout_s: float = DEFAULT_TIMEOUT_S
    contact: str | None = None
    """How an archive operator reaches a human about our traffic.

    Appended to the user agent when set. Optional, and worth setting: being
    reachable is the other half of being polite.
    """

    sources: Mapping[str, SourceSettings] = field(default_factory=dict)

    def for_source(self, source_id: str) -> SourceSettings:
        """This source's settings, or the defaults where the file said nothing.

        Args:
            source_id: A registered source.

        Returns:
            Its settings. Absent from the file means default, not disabled —
            the file is for departing from the defaults, not for repeating
            them.
        """
        return self.sources.get(source_id) or SourceSettings(source_id=source_id)

    def enabled_sources(self) -> tuple[str, ...]:
        """Every registered source this installation will fetch, in id order."""
        return tuple(
            source_id
            for source_id in sorted(REGISTRY)
            if self.for_source(source_id).enabled
        )


def find_config(environ: Mapping[str, str] | None = None) -> Path | None:
    """Where the settings file is, if there is one.

    Args:
        environ: Defaults to the process environment.

    Returns:
        The path :data:`CONFIG_ENV` names, or ``./ingest.toml`` if it exists,
        or None to run on defaults.

    Raises:
        ConfigurationError: :data:`CONFIG_ENV` names a file that is not there.
            Falling back to the defaults would run with settings the operator
            believes they wrote.
    """
    env = os.environ if environ is None else environ
    named = env.get(CONFIG_ENV, "").strip()
    if named:
        path = Path(named)
        if not path.is_file():
            message = f"{CONFIG_ENV} names {named!r}, which is not a readable file"
            raise ConfigurationError(message)
        return path
    beside = Path(DEFAULT_CONFIG_NAME)
    return beside if beside.is_file() else None


def load_settings(
    path: Path | None = None, environ: Mapping[str, str] | None = None
) -> IngestSettings:
    """Read the settings file, or return the defaults when there is none.

    Args:
        path: The file to read. Found via :func:`find_config` when omitted.
        environ: Defaults to the process environment, used only to find the
            file. No value is read from it here.

    Returns:
        The settings, with every relative path resolved.

    Raises:
        ConfigurationError: The file is not readable TOML, carries a key this
            version does not know, or holds a value that could not be obeyed.

    Note:
        Relative paths resolve against the **file's own directory**, not the
        working directory. ``raw_root = "data/ingest/raw"`` then means the same
        tree whichever directory the command is run from, which is what an
        operator reading the line assumes it means.
    """
    found = find_config(environ) if path is None else path
    if found is None:
        return IngestSettings(raw_root=DEFAULT_RAW_ROOT)
    try:
        table = tomllib.loads(found.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        message = f"{found} could not be read as TOML: {exc}"
        raise ConfigurationError(message) from exc

    _only_known(table, _TOP_LEVEL, str(found))
    return IngestSettings(
        raw_root=_raw_root(table, found),
        retry=_retry(table, found),
        timeout_s=_positive(table.get("timeout_s", DEFAULT_TIMEOUT_S), "timeout_s"),
        contact=_optional_text(table.get("contact"), "contact"),
        sources=_sources(table, found),
    )


def _only_known(
    table: Mapping[str, object], allowed: frozenset[str], where: str
) -> None:
    """Refuse a key this version does not know, naming the ones it does.

    A misspelled key that was ignored is a setting somebody believes they made,
    and the only moment it is cheap to find out otherwise is now.
    """
    unknown = sorted(set(table) - allowed)
    if unknown:
        known = ", ".join(sorted(allowed))
        plural = "keys" if len(unknown) > 1 else "key"
        message = f"{where}: unknown {plural} {unknown}; known: {known}"
        raise ConfigurationError(message)


def _raw_root(table: Mapping[str, object], found: Path) -> Path:
    """The raw store's root, resolved against the settings file."""
    value = table.get("raw_root")
    if value is None:
        return (found.parent / DEFAULT_RAW_ROOT).resolve()
    if not isinstance(value, str) or not value.strip():
        message = f"raw_root must be a non-empty path, not {value!r}"
        raise ConfigurationError(message)
    return (found.parent / value).resolve()


def _retry(table: Mapping[str, object], found: Path) -> RetryPolicy:
    """The backoff policy, held to its own rules rather than to this module's."""
    value = table.get("retry", {})
    if not isinstance(value, dict):
        message = f"retry must be a table, not {type(value).__name__}"
        raise ConfigurationError(message)
    _only_known(value, _RETRY_KEYS, f"{found} [retry]")
    try:
        return RetryPolicy(
            attempts=_whole(value.get("attempts", 4), "retry.attempts"),
            base_delay_s=_positive(
                value.get("base_delay_s", 1.0), "retry.base_delay_s"
            ),
            max_delay_s=_positive(value.get("max_delay_s", 30.0), "retry.max_delay_s"),
        )
    except ValueError as exc:
        message = f"{found} [retry]: {exc}"
        raise ConfigurationError(message) from exc


def _sources(table: Mapping[str, object], found: Path) -> dict[str, SourceSettings]:
    """Every configured source, each of which must be one that exists."""
    value = table.get("sources", {})
    if not isinstance(value, dict):
        message = f"sources must be a table, not {type(value).__name__}"
        raise ConfigurationError(message)
    unknown = sorted(set(value) - set(REGISTRY))
    if unknown:
        known = ", ".join(sorted(REGISTRY))
        message = (
            f"{found}: no adapter is registered for {unknown}; registered: {known}. "
            "A source configured but not implemented is settings that do nothing"
        )
        raise ConfigurationError(message)
    return {
        source_id: _source(source_id, entry, found)
        for source_id, entry in sorted(value.items())
    }


def _source(source_id: str, entry: object, found: Path) -> SourceSettings:
    """One ``[sources.<id>]`` table."""
    if not isinstance(entry, dict):
        message = f"[sources.{source_id}] must be a table, not {type(entry).__name__}"
        raise ConfigurationError(message)
    _only_known(entry, _SOURCE_KEYS, f"{found} [sources.{source_id}]")
    return SourceSettings(
        source_id=source_id,
        enabled=_flag(entry.get("enabled", True), f"sources.{source_id}.enabled"),
        base_url=_base_url(entry.get("base_url"), source_id),
        request_budget=_whole(
            entry.get("request_budget", DEFAULT_REQUEST_BUDGET),
            f"sources.{source_id}.request_budget",
        ),
        api_key_env=_api_key_env(entry.get("api_key_env"), source_id),
    )


def _api_key_env(value: object, source_id: str) -> str | None:
    """The name of a variable, and a loud refusal when it is a key instead.

    Anything that is not a plain ``SHOUTING_NAME`` is almost certainly the
    secret itself, pasted where its name belongs — which is a secret in a file
    that gets committed, and is the whole reason this indirection exists.
    """
    if value is None:
        return None
    if not isinstance(value, str) or not ENV_NAME.fullmatch(value):
        message = (
            f"sources.{source_id}.api_key_env must be the NAME of an environment "
            "variable, matching ^[A-Z][A-Z0-9_]*$ — not the key itself. If you have "
            "pasted a key here, remove it, rotate it, and set the variable instead"
        )
        raise ConfigurationError(message)
    return value


def _base_url(value: object, source_id: str) -> str | None:
    """An override for the adapter's root, https for the usual reason."""
    if value is None:
        return None
    if not isinstance(value, str) or not value.startswith("https://"):
        message = (
            f"sources.{source_id}.base_url must be an https URL, not {value!r}: over "
            "plain http an intermediary chooses what our digest records"
        )
        raise ConfigurationError(message)
    return value


def _flag(value: object, where: str) -> bool:
    if not isinstance(value, bool):
        message = f"{where} must be true or false, not {value!r}"
        raise ConfigurationError(message)
    return value


def _whole(value: object, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        message = f"{where} must be a whole number of at least 1, not {value!r}"
        raise ConfigurationError(message)
    return value


def _positive(value: object, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        message = f"{where} must be a positive number, not {value!r}"
        raise ConfigurationError(message)
    return float(value)


def _optional_text(value: object, where: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        message = f"{where} must be non-empty text, not {value!r}"
        raise ConfigurationError(message)
    return value.strip()
