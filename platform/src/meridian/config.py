"""Runtime configuration, read from the environment.

One source of truth for settings shared by the API, the migration runner and the
compose file — ``deploy/.env.example`` documents every value here.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from meridian.config_checks import EVERY_SECRET, check_settings
from meridian.secret_files import (
    PLACEHOLDER,
    InsecureConfigurationError,
    read_previous_secret,
    read_secret,
)

__all__ = [
    "InsecureConfigurationError",
    "Settings",
    "libpq_url",
    "load_settings",
    "sqlalchemy_url",
]

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})

_DRIVER = "+psycopg"
_SCHEMES = ("postgresql", "postgres")


@dataclass(frozen=True, slots=True)
class Settings:
    """Everything the platform needs to start."""

    database_url: str
    api_port: int
    api_log_level: str
    api_workers: int
    """How many processes ``meridian serve`` runs the API in (D-109)."""

    schedule_interval_s: int
    """Seconds between the scheduled jobs' rounds (D-110)."""
    schedule_horizon_s: int
    """How far ahead of now each round generates and schedules (D-110)."""
    schedule_config: str
    """The ``schedule.toml`` each round schedules under, or empty for its
    defaults: configuration A on the elevation proxy (D-168)."""
    jobs_metrics_port: int
    """Where the scheduled jobs serve their metrics, inside the network (D-109)."""

    token_hash_pepper: str
    token_hash_pepper_previous: str
    """The pepper being rotated away from, or empty (D-201).

    Accepted when verifying a bearer token or a registration key, never used to
    hash anything new. A credential it verifies is re-hashed under
    ``token_hash_pepper`` on the spot, so the overlap drains as stations call.
    """
    registration_invite_token: str
    registration_recovery_window_s: int
    heartbeat_interval_s: int

    metrics_token: str
    """Bearer token the ``/metrics`` scrape must present (D-087).

    The endpoint carries process internals and is served from the same public
    hostname as everything else, so it is closed by default rather than opened
    when someone notices. Prometheus reads the same value from a credentials
    file inside the compose network.
    """

    public_base_url: str
    tunnel_hostname: str
    public_mode: bool

    rate_limits: bool
    """Whether the API limits request rates in the process (D-202)."""
    client_address_header: str
    """The header naming the caller's address, or empty for the peer (D-202).

    Trusted only because the deployment that sets it publishes no port the edge
    does not front; set anywhere else, it lets a caller choose its own address.
    """

    grafana_admin_password: str

    simulator_seed: int
    simulator_station_count: int

    @property
    def psycopg_url(self) -> str:
        """``database_url`` in the form ``psycopg.connect`` accepts (D-033)."""
        return libpq_url(self.database_url)

    @property
    def database_password(self) -> str:
        """The password actually used to connect, whatever supplied it.

        ``DATABASE_URL`` carries its own password and takes precedence over the
        ``POSTGRES_*`` parts (see :func:`_database_url`), so reading
        ``POSTGRES_PASSWORD`` from the environment answers a different question
        than "what will this process authenticate with" whenever both are set —
        which is the normal case under compose, where the URL is assembled in the
        YAML and the separate variable is never passed to the API at all.
        """
        return urlparse(self.psycopg_url).password or ""

    @property
    def is_public(self) -> bool:
        """Whether this deployment is reachable from outside the machine.

        Three independent signals, any of which is sufficient:

        ``MERIDIAN_PUBLIC``
            Declared outright. Compose derives it from the presence of a
            Cloudflare tunnel token, because the ``public`` profile can start the
            tunnel with neither ``TUNNEL_HOSTNAME`` nor ``PUBLIC_BASE_URL`` set —
            and did, which meant the platform was publicly reachable while this
            property still answered ``False``.
        ``TUNNEL_HOSTNAME``
            A tunnel is what makes the platform reachable, whatever the base URL
            says.
        ``PUBLIC_BASE_URL``
            Anything but a loopback host.
        """
        if self.public_mode or self.tunnel_hostname:
            return True
        host = urlparse(self.public_base_url).hostname
        return host is not None and host not in _LOOPBACK_HOSTS


def _split_scheme(url: str) -> tuple[str, str]:
    """Split a URL into its scheme and everything after ``://``."""
    scheme, separator, rest = url.partition("://")
    if not separator:
        raise InsecureConfigurationError(f"DATABASE_URL is not a URL: {url!r}")
    return scheme, rest


def libpq_url(url: str) -> str:
    """Return ``url`` in the form ``psycopg.connect`` accepts.

    Strips a SQLAlchemy driver suffix: ``postgresql+psycopg://…`` becomes
    ``postgresql://…``. libpq rejects the ``+psycopg`` part outright.
    """
    scheme, rest = _split_scheme(url)
    base, _, _ = scheme.partition("+")
    return f"{base}://{rest}"


def sqlalchemy_url(url: str) -> str:
    """Return ``url`` in the form Alembic accepts.

    Adds the driver suffix if it is absent, so ``postgresql://…`` becomes
    ``postgresql+psycopg://…``. Without it SQLAlchemy reaches for ``psycopg2``,
    which is not installed and never will be.

    **The pair with :func:`libpq_url` exists so there is one ``DATABASE_URL``.**
    Before D-033 the compose file set the same variable name to two different
    values — one for the migration runner, one for the API — which meant CI, with
    a single value, could not both migrate and query. Two names for one
    connection is how a staging database gets migrated and a production one
    queried, and nothing ever detects it.
    """
    scheme, rest = _split_scheme(url)
    if "+" in scheme:
        return url
    if scheme not in _SCHEMES:
        raise InsecureConfigurationError(
            f"DATABASE_URL scheme must be postgresql, got {scheme!r}"
        )
    return f"{scheme}{_DRIVER}://{rest}"


_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"", "0", "false", "no", "off"})


def _bool_env(name: str, default: bool) -> bool:
    """Read a boolean variable, refusing a spelling that is neither true nor false."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    accepted = sorted((_TRUE | _FALSE) - {""})
    raise InsecureConfigurationError(
        f"{name} must be empty or one of {accepted}, got {raw!r}"
    )


def _int_env(name: str, default: int) -> int:
    """Read an integer variable, treating unset and empty alike as the default."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise InsecureConfigurationError(
            f"{name} must be an integer, got {raw!r}"
        ) from exc


_HEADER_NAME = re.compile(r"[A-Za-z0-9-]*")


def _header_name_env(name: str) -> str:
    """Read an HTTP header name, lower-cased as ASGI presents headers."""
    raw = os.environ.get(name, "").strip()
    if not _HEADER_NAME.fullmatch(raw):
        raise InsecureConfigurationError(f"{name} is not a header name: {raw!r}")
    return raw.lower()


def _database_url() -> str:
    """``DATABASE_URL`` if set, else one assembled from the ``POSTGRES_*`` parts."""
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        return url
    user = os.environ.get("POSTGRES_USER", "meridian")
    password = os.environ.get("POSTGRES_PASSWORD", "")
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    database = os.environ.get("POSTGRES_DB", "meridian")
    return f"postgresql://{user}:{password}@{host}:{port}/{database}"


def load_settings(*, secrets_held: frozenset[str] = EVERY_SECRET) -> Settings:
    """Read settings from the environment and refuse an unsafe combination.

    Reads ``os.environ`` directly and takes no override mapping, so a test sets
    a variable the same way a deployment does — ``monkeypatch.setenv``, which
    unsets it again afterwards. See ``tests/unit/test_config.py``.

    ``secrets_held`` names the secrets this process is given, by the names in the
    placeholder refusal. Only those are refused as placeholders; every other
    check applies whatever the process holds (D-206).

    **The platform will not start with placeholder secrets on a public address.**
    D-006 argues that shipping an unauthenticated write endpoint to a public
    address is not a defensible position; shipping ``change-me`` to one is the
    same position with an extra step. The check is here rather than in a runbook
    because a runbook step is one nobody performs at 2 a.m. in week 20.

    Placeholders are fine on loopback — that is what makes ``cp .env.example .env
    && docker compose up`` work out of the box, which the ten-minute bring-up
    requirement needs.

    **A second refusal has nothing to do with secrets and fires everywhere.**
    ``HEARTBEAT_INTERVAL_S`` past 30 s leaves SC-5's fixed liveness thresholds
    describing something other than "two and three missed heartbeats", and a
    healthy station then reads ``stale`` for part of every cycle. That is a wrong
    number rather than an exposure, so loopback is not an excuse — see
    :func:`_refuse_an_interval_liveness_cannot_describe`.
    """
    settings = Settings(
        database_url=_database_url(),
        api_port=_int_env("API_PORT", 8000),
        api_log_level=os.environ.get("API_LOG_LEVEL", "info"),
        api_workers=_int_env("API_WORKERS", 1),
        # Five minutes and six hours, the values the sim profile's scheduling
        # loop ran on since Stage 10: a pass is at least eight minutes long, so
        # a round every five cannot let one rise unscheduled, and six hours keeps
        # D-026's two-hour delivery horizon filled with room to spare.
        schedule_interval_s=_int_env("SCHEDULE_INTERVAL_S", 300),
        schedule_horizon_s=_int_env("SCHEDULE_HORIZON_S", 6 * 3600),
        schedule_config=os.environ.get("SCHEDULE_CONFIG", "").strip(),
        # Never published outside the compose network, so nothing depends on
        # the number itself beyond Prometheus's scrape configuration agreeing.
        jobs_metrics_port=_int_env("JOBS_METRICS_PORT", 9464),
        token_hash_pepper=read_secret("TOKEN_HASH_PEPPER", PLACEHOLDER),
        token_hash_pepper_previous=read_previous_secret("TOKEN_HASH_PEPPER_PREVIOUS"),
        registration_invite_token=read_secret("REGISTRATION_INVITE_TOKEN", PLACEHOLDER),
        # D-023: how long after registering a station may still recover a lost
        # bearer token by re-presenting its invite and registration key. One hour
        # by default, AND only while no heartbeat has arrived — both conditions.
        # A station past either one rotates through a bound invite instead, which
        # this window does not govern (D-034).
        registration_recovery_window_s=_int_env("REGISTRATION_RECOVERY_WINDOW_S", 3600),
        heartbeat_interval_s=_int_env("HEARTBEAT_INTERVAL_S", 30),
        metrics_token=read_secret("METRICS_TOKEN", PLACEHOLDER),
        public_base_url=os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000"),
        tunnel_hostname=os.environ.get("TUNNEL_HOSTNAME", "").strip(),
        public_mode=_bool_env("MERIDIAN_PUBLIC", False),
        rate_limits=_bool_env("RATE_LIMITS", True),
        client_address_header=_header_name_env("CLIENT_ADDRESS_HEADER"),
        grafana_admin_password=os.environ.get("GRAFANA_ADMIN_PASSWORD", PLACEHOLDER),
        simulator_seed=_int_env("SIMULATOR_SEED", 4471),
        simulator_station_count=_int_env("SIMULATOR_STATION_COUNT", 1),
    )

    check_settings(settings, secrets_held)
    return settings
