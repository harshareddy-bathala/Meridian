"""The checks :func:`meridian.config.load_settings` runs on what it read.

Split from :mod:`meridian.config`, which had reached its size limit. Each check
either returns or raises :class:`InsecureConfigurationError` with the variable to
fix, so a misconfiguration stops the process at start-up with its name.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from meridian.log_redaction import remember_secrets
from meridian.registry.liveness import (
    MAX_HEARTBEAT_INTERVAL_S,
    is_heartbeat_interval_consistent,
)
from meridian.secret_files import PLACEHOLDER, InsecureConfigurationError

if TYPE_CHECKING:
    from meridian.config import Settings

__all__ = [
    "DATABASE_PASSWORD",
    "EVERY_SECRET",
    "METRICS_TOKEN",
    "check_settings",
    "secrets_of",
]

PEPPER = "TOKEN_HASH_PEPPER"
INVITE = "REGISTRATION_INVITE_TOKEN"
DATABASE_PASSWORD = "DATABASE_URL password"
GRAFANA_PASSWORD = "GRAFANA_ADMIN_PASSWORD"
METRICS_TOKEN = "METRICS_TOKEN"

EVERY_SECRET = frozenset(
    {PEPPER, INVITE, DATABASE_PASSWORD, GRAFANA_PASSWORD, METRICS_TOKEN}
)
"""What a process is assumed to hold unless it says otherwise (D-206).

The API holds all of them, or answers for them: Grafana's password is checked
there because nothing else in the platform can see it. The jobs process holds
only the database password and the metrics token, and is given only those, so
checking the rest there refused every public start of it.
"""


def check_settings(settings: Settings, held: frozenset[str] = EVERY_SECRET) -> None:
    """Remember every secret for redaction, then refuse an unsafe combination.

    Args:
        settings: What was read.
        held: The secrets this process holds, by the names in the refusal.
    """
    # Before any refusal, so a message that quotes a value cannot print one.
    # Every process that loads settings holds these, so every one redacts them
    # (D-204).
    remember_secrets(secrets_of(settings))
    _refuse_an_interval_liveness_cannot_describe(settings.heartbeat_interval_s)
    _refuse_a_rotation_that_did_not_happen(settings)

    if settings.is_public:
        _refuse_placeholder_secrets(settings, held)
        _refuse_unlimited_public_start(settings)


def secrets_of(settings: Settings) -> tuple[str, ...]:
    """Every secret value a process holds once it has loaded ``settings``.

    The placeholder is left out: it is published in ``.env.example``, and
    redacting it would hide the very word the placeholder refusal names.
    """
    held = (
        settings.token_hash_pepper,
        settings.token_hash_pepper_previous,
        settings.registration_invite_token,
        settings.metrics_token,
        settings.grafana_admin_password,
        settings.database_password,
    )
    return tuple(value for value in held if value and value != PLACEHOLDER)


def _refuse_an_interval_liveness_cannot_describe(heartbeat_interval_s: int) -> None:
    """Raise if the configured interval would make a healthy station read stale."""
    # Checked on every start, not only a public one, unlike the placeholder
    # refusal below. A placeholder secret on loopback exposes nothing; an
    # interval past this ceiling produces *wrong numbers* on any deployment,
    # and CLAUDE.md's seventh rule makes liveness load-bearing for every
    # reliability figure in the project. A wrong figure on a laptop is the one
    # that gets copied into a report.
    if is_heartbeat_interval_consistent(heartbeat_interval_s):
        return
    raise InsecureConfigurationError(
        f"Refusing to start: HEARTBEAT_INTERVAL_S is {heartbeat_interval_s}, and "
        f"liveness thresholds are fixed at 60 s and 90 s by SC-5 (D-013). Above "
        f"{MAX_HEARTBEAT_INTERVAL_S} s a station heartbeating exactly on time "
        "spends part of every cycle reading 'stale'. Use 30, which is what "
        "D-030 fixes for all of MSP 0.x."
    )


def _refuse_a_rotation_that_did_not_happen(settings: Settings) -> None:
    """Raise if the previous pepper is the current one (D-201).

    Harmless to run, and always a mistake: the step that writes the new pepper
    was skipped, and the operator believes a leaked pepper has been replaced.
    """
    if settings.token_hash_pepper_previous != settings.token_hash_pepper:
        return
    raise InsecureConfigurationError(
        "Refusing to start: TOKEN_HASH_PEPPER_PREVIOUS is the same as "
        "TOKEN_HASH_PEPPER, so the rotation has not happened. Write the new "
        "pepper first; docs/OPERATIONS.md § Rotating secrets."
    )


def _refuse_unlimited_public_start(settings: Settings) -> None:
    """Raise if rate limits are off on a publicly reachable deployment (D-202)."""
    if settings.rate_limits:
        return
    raise InsecureConfigurationError(
        "Refusing to start: RATE_LIMITS is off while the platform is publicly "
        "reachable (D-202). It exists for accelerated simulations on loopback."
    )


def _refuse_placeholder_secrets(settings: Settings, held: frozenset[str]) -> None:
    """Raise if any secret is still ``change-me`` on a publicly reachable deployment."""
    # The database password is read back out of the URL the process will
    # actually connect with, not from POSTGRES_PASSWORD. Compose embeds the
    # password inside DATABASE_URL and never passes the separate variable to
    # the API, so a check against the variable passed a deployment carrying
    # `change-me` in the URL it was about to use.
    #
    # GRAFANA_ADMIN_PASSWORD is checked even though Grafana runs only under
    # `--profile metrics` and this process cannot see which profiles are up.
    # That is deliberately over-strict: compose publishes Grafana on host
    # :3001, `--profile public --profile metrics` with a copied .env produced a
    # platform that started cleanly beside an admin console on `admin/change-me`,
    # and the cost of the strict direction is a refusal with a message saying
    # exactly which variable to set.
    placeholders = [
        name
        for name, value in (
            (PEPPER, settings.token_hash_pepper),
            (INVITE, settings.registration_invite_token),
            (DATABASE_PASSWORD, settings.database_password),
            (GRAFANA_PASSWORD, settings.grafana_admin_password),
            # D-087. A placeholder here is worse than a placeholder elsewhere:
            # the token is the only thing standing between a public hostname and
            # the process internals, and `change-me` is the first value anyone
            # guessing would try.
            (METRICS_TOKEN, settings.metrics_token),
        )
        if value == PLACEHOLDER and name in held
    ]
    if not placeholders:
        return
    raise InsecureConfigurationError(
        "Refusing to start: "
        + ", ".join(placeholders)
        + f" still set to {PLACEHOLDER!r} while the platform is publicly "
        "reachable. Generate each with: openssl rand -hex 32"
    )
