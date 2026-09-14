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

from pathlib import Path

import pytest

from meridian.config import InsecureConfigurationError, Settings, load_settings

REAL_PEPPER = "0f9a" * 16
REAL_INVITE = "7c31" * 16
REAL_PASSWORD = "e41b" * 16
REAL_GRAFANA_PASSWORD = "b28d" * 16
REAL_METRICS_TOKEN = "5ae6" * 16

BASE_ENV = {
    "DATABASE_URL": "postgresql://meridian:change-me@db:5432/meridian",
    "TOKEN_HASH_PEPPER": "change-me",
    "REGISTRATION_INVITE_TOKEN": "change-me",
    "GRAFANA_ADMIN_PASSWORD": "change-me",
    "METRICS_TOKEN": "change-me",
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
    "HEARTBEAT_INTERVAL_S",
    "GRAFANA_ADMIN_PASSWORD",
    "METRICS_TOKEN",
    "METRICS_TOKEN_FILE",
    "TOKEN_HASH_PEPPER_FILE",
    "REGISTRATION_INVITE_TOKEN_FILE",
    "API_WORKERS",
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
        "GRAFANA_ADMIN_PASSWORD": REAL_GRAFANA_PASSWORD,
        "METRICS_TOKEN": REAL_METRICS_TOKEN,
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


def test_placeholders_are_refused_on_a_public_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(InsecureConfigurationError) as excinfo:
        _load(monkeypatch, PUBLIC_BASE_URL="https://meridian.example.org")

    message = str(excinfo.value)
    assert "TOKEN_HASH_PEPPER" in message
    assert "REGISTRATION_INVITE_TOKEN" in message
    assert "DATABASE_URL password" in message
    assert "GRAFANA_ADMIN_PASSWORD" in message
    assert "METRICS_TOKEN" in message


def test_a_placeholder_metrics_token_alone_refuses_a_public_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every other secret real, and the platform still must not start.

    `/metrics` publishes process internals and is served from the same public
    hostname as everything else (D-087). `change-me` is the first value anyone
    probing would try, so a deployment carrying it is not meaningfully closed —
    which makes this the same position D-006 refuses, one step removed.
    """
    with pytest.raises(InsecureConfigurationError) as excinfo:
        _load(
            monkeypatch,
            **_secure(METRICS_TOKEN="change-me"),
            PUBLIC_BASE_URL="https://meridian.example.org",
        )

    message = str(excinfo.value)
    assert "METRICS_TOKEN" in message
    assert "TOKEN_HASH_PEPPER" not in message


def test_a_placeholder_metrics_token_is_fine_on_loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`cp .env.example .env && docker compose up` must still work untouched.

    A /metrics reachable only from the machine it runs on exposes nothing, and
    the ten-minute bring-up depends on the shipped defaults being usable.
    """
    settings = _load(monkeypatch, PUBLIC_BASE_URL="http://localhost:8000")

    assert settings.metrics_token == "change-me"
    assert not settings.is_public


def test_a_tunnel_hostname_alone_counts_as_public(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tunnel is what makes the platform reachable, whatever the base URL says.

    Without this, a station left with PUBLIC_BASE_URL=localhost and a live tunnel
    would be publicly exposed with placeholder secrets and no complaint — which is
    exactly the SC-6 configuration.
    """
    with pytest.raises(InsecureConfigurationError):
        _load(
            monkeypatch,
            PUBLIC_BASE_URL="http://localhost:8000",
            TUNNEL_HOSTNAME="meridian.example.org",
        )


def test_meridian_public_alone_counts_as_public(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--profile public` needs no hostname to reach the internet.

    ``docker compose --profile public up`` starts cloudflared as soon as
    CLOUDFLARE_TUNNEL_TOKEN is set, and neither TUNNEL_HOSTNAME nor
    PUBLIC_BASE_URL has to be touched for that to work. The deployment was then
    publicly reachable while ``is_public`` answered False and the placeholder
    check never ran. Compose derives MERIDIAN_PUBLIC from the tunnel token so the
    one variable the tunnel cannot start without is the one that arms the check.
    """
    with pytest.raises(InsecureConfigurationError):
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
    with pytest.raises(InsecureConfigurationError) as excinfo:
        _load(
            monkeypatch,
            **_secure(DATABASE_URL="postgresql://meridian:change-me@db:5432/meridian"),
            PUBLIC_BASE_URL="https://meridian.example.org",
            POSTGRES_PASSWORD=REAL_PASSWORD,  # set, real, and not what is used
        )

    message = str(excinfo.value)
    assert "DATABASE_URL password" in message
    assert "TOKEN_HASH_PEPPER" not in message
    assert "GRAFANA_ADMIN_PASSWORD" not in message


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

    with pytest.raises(InsecureConfigurationError, match="DATABASE_URL password"):
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
    with pytest.raises(InsecureConfigurationError, match="MERIDIAN_PUBLIC"):
        _load(monkeypatch, MERIDIAN_PUBLIC="maybe")


def test_a_placeholder_grafana_password_alone_refuses_a_public_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every other secret real, and the platform still must not start.

    `--profile public --profile metrics` with a copied .env produced exactly
    this: a platform that started cleanly beside a Grafana published on host
    :3001 with `admin/change-me`. The variable is passed to the api service for
    no reason other than to let this check see it.
    """
    with pytest.raises(InsecureConfigurationError) as excinfo:
        _load(
            monkeypatch,
            **_secure(GRAFANA_ADMIN_PASSWORD="change-me"),
            PUBLIC_BASE_URL="https://meridian.example.org",
        )

    message = str(excinfo.value)
    assert "GRAFANA_ADMIN_PASSWORD" in message
    assert "TOKEN_HASH_PEPPER" not in message


def test_a_placeholder_grafana_password_is_fine_on_loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`cp .env.example .env && docker compose --profile metrics up` still works.

    The strict direction is only strict where it matters. A Grafana on a laptop's
    own loopback is the development case the ten-minute bring-up depends on.
    """
    settings = _load(monkeypatch, PUBLIC_BASE_URL="http://localhost:8000")

    assert not settings.is_public
    assert settings.grafana_admin_password == "change-me"


def test_a_heartbeat_interval_liveness_cannot_describe_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fires on loopback too, unlike the placeholder refusal.

    SC-5 fixes `stale` at 60 s and `offline` at 90 s (D-013). At a 45 s
    interval a station heartbeating exactly on time is more than 60 s past its
    last heartbeat for part of every cycle, so it flaps into `stale` while
    behaving as specified — and every reliability figure reading liveness
    inherits that. A wrong number is not excused by being local.
    """
    with pytest.raises(InsecureConfigurationError) as excinfo:
        _load(
            monkeypatch,
            PUBLIC_BASE_URL="http://localhost:8000",
            HEARTBEAT_INTERVAL_S="45",
        )

    message = str(excinfo.value)
    assert "HEARTBEAT_INTERVAL_S" in message
    assert "30" in message


def test_the_default_heartbeat_interval_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D-030 fixes 30 s for all of MSP 0.x, and it must survive its own check.

    A consistency check that rejected the documented default would be found by
    whoever ran `docker compose up` on a clean machine.
    """
    settings = _load(monkeypatch, PUBLIC_BASE_URL="http://localhost:8000")

    assert settings.heartbeat_interval_s == 30


def test_a_secret_file_wins_over_the_variable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Mounting a file overrides a value left in ``.env`` (D-114).

    The trailing newline ``openssl rand -hex 32 > file`` writes is not part of
    the secret.
    """
    secret = tmp_path / "metrics_token"
    secret.write_text(REAL_METRICS_TOKEN + "\n", encoding="utf-8")

    settings = _load(
        monkeypatch,
        METRICS_TOKEN="a-value-left-in-env",
        METRICS_TOKEN_FILE=str(secret),
    )

    assert settings.metrics_token == REAL_METRICS_TOKEN


def test_a_placeholder_read_from_a_file_is_still_refused_publicly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The refusal applies to what was read, whichever way it arrived."""
    pepper = tmp_path / "pepper"
    pepper.write_text("change-me\n", encoding="utf-8")

    with pytest.raises(InsecureConfigurationError, match="TOKEN_HASH_PEPPER"):
        _load(
            monkeypatch,
            **_secure(
                TOKEN_HASH_PEPPER_FILE=str(pepper),
                PUBLIC_BASE_URL="https://meridian.example.org",
            ),
        )


@pytest.mark.parametrize("contents", [None, "", "   \n"])
def test_an_unreadable_or_empty_secret_file_refuses_to_start(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, contents: str | None
) -> None:
    """Falling back to the variable would start on a secret believed replaced."""
    secret = tmp_path / "invite"
    if contents is not None:
        secret.write_text(contents, encoding="utf-8")

    with pytest.raises(InsecureConfigurationError, match="REGISTRATION_INVITE_TOKEN"):
        _load(monkeypatch, REGISTRATION_INVITE_TOKEN_FILE=str(secret))


def test_the_worker_count_defaults_to_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """One worker needs no multiprocess directory, so it is the safe default."""
    assert _load(monkeypatch).api_workers == 1
    assert _load(monkeypatch, API_WORKERS="3").api_workers == 3
