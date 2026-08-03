"""The platform refuses to expose placeholder secrets publicly.

D-006 argues that shipping an unauthenticated write endpoint to a public address
is not defensible. Shipping ``change-me`` to one is the same position with an
extra step, so the refusal is enforced at boot rather than written in a runbook.

A refusal is only worth having if it cannot be walked around, and two ways round
it existed. Both are covered below: a tunnel started with nothing else set, and a
placeholder password carried inside ``DATABASE_URL`` rather than in the variable
that was being read.
"""

from __future__ import annotations

import pytest

from meridian.config import InsecureConfiguration, Settings, load_settings

REAL_PEPPER = "0f9a" * 16
REAL_INVITE = "7c31" * 16
REAL_PASSWORD = "e41b" * 16

BASE_ENV = {
    "DATABASE_URL": "postgresql://meridian:change-me@db:5432/meridian",
    "TOKEN_HASH_PEPPER": "change-me",
    "REGISTRATION_INVITE_TOKEN": "change-me",
}

MANAGED = (
    *BASE_ENV,
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_DB",
    "PUBLIC_BASE_URL",
    "TUNNEL_HOSTNAME",
    "MERIDIAN_PUBLIC",
    "API_PORT",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """``load_settings`` reads ``os.environ`` directly, so leakage between tests
    is a real risk — including from the developer's own shell."""
    for key in MANAGED:
        monkeypatch.delenv(key, raising=False)


def _load(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> Settings:
    for key, value in {**BASE_ENV, **overrides}.items():
        monkeypatch.setenv(key, value)
    return load_settings()


def _secure(**overrides: str) -> dict[str, str]:
    return {
        "DATABASE_URL": f"postgresql://meridian:{REAL_PASSWORD}@db:5432/meridian",
        "TOKEN_HASH_PEPPER": REAL_PEPPER,
        "REGISTRATION_INVITE_TOKEN": REAL_INVITE,
        **overrides,
    }


def test_placeholders_are_allowed_on_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    """`cp .env.example .env && docker compose up` must work out of the box.

    The ten-minute bring-up requirement depends on the defaults being usable, so
    the refusal must not fire for local development.
    """
    settings = _load(monkeypatch, PUBLIC_BASE_URL="http://localhost:8000")
    assert not settings.is_public
    assert settings.heartbeat_interval_s == 30


def test_placeholders_are_refused_on_a_public_address(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(InsecureConfiguration) as excinfo:
        _load(monkeypatch, PUBLIC_BASE_URL="https://meridian.example.org")

    message = str(excinfo.value)
    assert "TOKEN_HASH_PEPPER" in message
    assert "REGISTRATION_INVITE_TOKEN" in message
    assert "DATABASE_URL password" in message


def test_a_tunnel_hostname_alone_counts_as_public(monkeypatch: pytest.MonkeyPatch) -> None:
    """The tunnel is what makes the platform reachable, whatever the base URL says.

    Without this, a station left with PUBLIC_BASE_URL=localhost and a live tunnel
    would be publicly exposed with placeholder secrets and no complaint — which is
    exactly the SC-6 configuration.
    """
    with pytest.raises(InsecureConfiguration):
        _load(
            monkeypatch,
            PUBLIC_BASE_URL="http://localhost:8000",
            TUNNEL_HOSTNAME="meridian.example.org",
        )


def test_meridian_public_alone_counts_as_public(monkeypatch: pytest.MonkeyPatch) -> None:
    """`--profile public` needs no hostname to reach the internet.

    ``docker compose --profile public up`` starts cloudflared as soon as
    CLOUDFLARE_TUNNEL_TOKEN is set, and neither TUNNEL_HOSTNAME nor
    PUBLIC_BASE_URL has to be touched for that to work. The deployment was then
    publicly reachable while ``is_public`` answered False and the placeholder
    check never ran. Compose derives MERIDIAN_PUBLIC from the tunnel token so the
    one variable the tunnel cannot start without is the one that arms the check.
    """
    with pytest.raises(InsecureConfiguration):
        _load(monkeypatch, PUBLIC_BASE_URL="http://localhost:8000", MERIDIAN_PUBLIC="1")


def test_a_placeholder_password_inside_database_url_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The password checked must be the one the process will connect with.

    Compose embeds the password in DATABASE_URL and does not pass
    POSTGRES_PASSWORD to the api service at all, so reading that variable checked
    something the deployment was not using — and a public stack with `change-me`
    in its database URL started without complaint.
    """
    with pytest.raises(InsecureConfiguration) as excinfo:
        _load(
            monkeypatch,
            **_secure(DATABASE_URL="postgresql://meridian:change-me@db:5432/meridian"),
            PUBLIC_BASE_URL="https://meridian.example.org",
            POSTGRES_PASSWORD=REAL_PASSWORD,  # set, real, and not what is used
        )

    message = str(excinfo.value)
    assert "DATABASE_URL password" in message
    assert "TOKEN_HASH_PEPPER" not in message


def test_postgres_password_still_covered_when_no_database_url_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without DATABASE_URL the URL is assembled from the POSTGRES_* parts.

    Checking the assembled URL rather than the variable covers both sources with
    one rule, which is the point — there is no path that authenticates with a
    password nobody inspected.
    """
    monkeypatch.setenv("TOKEN_HASH_PEPPER", REAL_PEPPER)
    monkeypatch.setenv("REGISTRATION_INVITE_TOKEN", REAL_INVITE)
    monkeypatch.setenv("POSTGRES_PASSWORD", "change-me")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://meridian.example.org")

    with pytest.raises(InsecureConfiguration, match="DATABASE_URL password"):
        load_settings()


def test_real_secrets_pass_on_a_public_address(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _load(
        monkeypatch,
        **_secure(),
        PUBLIC_BASE_URL="https://meridian.example.org",
    )
    assert settings.is_public
    assert settings.database_password == REAL_PASSWORD


def test_public_mode_off_is_not_public(monkeypatch: pytest.MonkeyPatch) -> None:
    """Compose interpolates MERIDIAN_PUBLIC to the empty string when no tunnel
    token is set, so the empty string must read as off rather than as garbage."""
    settings = _load(monkeypatch, MERIDIAN_PUBLIC="")
    assert not settings.public_mode
    assert not settings.is_public


def test_an_unparseable_public_flag_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """`MERIDIAN_PUBLIC=maybe` must not quietly mean False.

    A misspelling that disables a security check by defaulting to off is the
    failure this whole check exists to avoid.
    """
    with pytest.raises(InsecureConfiguration, match="MERIDIAN_PUBLIC"):
        _load(monkeypatch, MERIDIAN_PUBLIC="maybe")
