"""Runtime configuration, read from the environment.

One source of truth for settings shared by the API, the migration runner and the
compose file — ``deploy/.env.example`` documents every value here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

__all__ = [
    "InsecureConfiguration",
    "Settings",
    "libpq_url",
    "load_settings",
    "sqlalchemy_url",
]

PLACEHOLDER = "change-me"

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})

_DRIVER = "+psycopg"
_SCHEMES = ("postgresql", "postgres")


class InsecureConfiguration(RuntimeError):
    """Raised when a placeholder secret would be exposed publicly."""


@dataclass(frozen=True, slots=True)
class Settings:
    """Everything the platform needs to start."""

    database_url: str
    api_port: int
    api_log_level: str

    token_hash_pepper: str
    registration_invite_token: str
    registration_recovery_window_s: int
    heartbeat_interval_s: int

    public_base_url: str
    tunnel_hostname: str
    public_mode: bool

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
    scheme, separator, rest = url.partition("://")
    if not separator:
        raise InsecureConfiguration(f"DATABASE_URL is not a URL: {url!r}")
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
        raise InsecureConfiguration(f"DATABASE_URL scheme must be postgresql, got {scheme!r}")
    return f"{scheme}{_DRIVER}://{rest}"


_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"", "0", "false", "no", "off"})


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    accepted = sorted((_TRUE | _FALSE) - {""})
    raise InsecureConfiguration(f"{name} must be empty or one of {accepted}, got {raw!r}")


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise InsecureConfiguration(f"{name} must be an integer, got {raw!r}") from exc


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        return url
    user = os.environ.get("POSTGRES_USER", "meridian")
    password = os.environ.get("POSTGRES_PASSWORD", "")
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    database = os.environ.get("POSTGRES_DB", "meridian")
    return f"postgresql://{user}:{password}@{host}:{port}/{database}"


def load_settings(*, env: dict[str, str] | None = None) -> Settings:
    """Read settings from the environment and refuse an unsafe combination.

    **The platform will not start with placeholder secrets on a public address.**
    D-006 argues that shipping an unauthenticated write endpoint to a public
    address is not a defensible position; shipping ``change-me`` to one is the
    same position with an extra step. The check is here rather than in a runbook
    because a runbook step is one nobody performs at 2 a.m. in week 20.

    Placeholders are fine on loopback — that is what makes ``cp .env.example .env
    && docker compose up`` work out of the box, which the ten-minute bring-up
    requirement needs.
    """
    if env is not None:
        os.environ.update(env)

    settings = Settings(
        database_url=_database_url(),
        api_port=_int_env("API_PORT", 8000),
        api_log_level=os.environ.get("API_LOG_LEVEL", "info"),
        token_hash_pepper=os.environ.get("TOKEN_HASH_PEPPER", PLACEHOLDER),
        registration_invite_token=os.environ.get("REGISTRATION_INVITE_TOKEN", PLACEHOLDER),
        # D-023: how long after registering a station may still recover a lost
        # bearer token by re-presenting its invite and registration key. One hour
        # by default, AND only while no heartbeat has arrived — both conditions.
        # A station past either one rotates through a bound invite instead, which
        # this window does not govern (D-034).
        registration_recovery_window_s=_int_env("REGISTRATION_RECOVERY_WINDOW_S", 3600),
        heartbeat_interval_s=_int_env("HEARTBEAT_INTERVAL_S", 30),
        public_base_url=os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000"),
        tunnel_hostname=os.environ.get("TUNNEL_HOSTNAME", "").strip(),
        public_mode=_bool_env("MERIDIAN_PUBLIC", False),
        simulator_seed=_int_env("SIMULATOR_SEED", 4471),
        simulator_station_count=_int_env("SIMULATOR_STATION_COUNT", 1),
    )

    if settings.is_public:
        # The database password is read back out of the URL the process will
        # actually connect with, not from POSTGRES_PASSWORD. Compose embeds the
        # password inside DATABASE_URL and never passes the separate variable to
        # the API, so a check against the variable passed a deployment carrying
        # `change-me` in the URL it was about to use.
        placeholders = [
            name
            for name, value in (
                ("TOKEN_HASH_PEPPER", settings.token_hash_pepper),
                ("REGISTRATION_INVITE_TOKEN", settings.registration_invite_token),
                ("DATABASE_URL password", settings.database_password),
            )
            if value == PLACEHOLDER
        ]
        if placeholders:
            raise InsecureConfiguration(
                "Refusing to start: "
                + ", ".join(placeholders)
                + f" still set to {PLACEHOLDER!r} while the platform is publicly "
                "reachable. Generate each with: openssl rand -hex 32"
            )

    return settings
