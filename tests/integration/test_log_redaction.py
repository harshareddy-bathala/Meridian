"""No secret appears in any log line the platform writes (D-204).

The platform is started with the logging configuration ``meridian serve`` and
``meridian jobs run`` install, writing to a buffer at ``debug``, the most
talkative level. Then everything that could plausibly put a credential in a log
line is done to it through its own endpoints: a registration, a malformed one
carrying an invite token and a registration key, a refused recovery, heartbeats
with a good token, a wrong one and a malformed body, a scrape with a wrong metrics
token, the public API, and a restart under a rotated pepper, whose re-hash is
logged. Every line written is then searched for every secret involved.

**The positive control is the same flow without the filter.** It must find at
least one secret, or the search is proving nothing: a test that passes because
no line mentioned anything looks exactly like one that passes because every line
was redacted.

Every write is rolled back.
"""

from __future__ import annotations

import io
import logging
import logging.config
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from meridian.api.app import create_app
from meridian.api.dependencies import get_connection
from meridian.cli_serve import logging_configuration
from meridian.log_redaction import REDACTED, forget_secrets
from meridian.store.invites import hash_invite_token

CURRENT = {"MSP-Version": "0.1"}
PEPPER = "log-redaction-pepper-0f9a0f9a0f9a0f9a"
METRICS_TOKEN = "log-redaction-metrics-token-5ae65ae6"
BOOTSTRAP_INVITE = "log-redaction-bootstrap-invite-b28d"
STATION_INVITE = "log-redaction-station-invite-e41be41b"
REGISTRATION_KEY = "log-redaction-registration-key-3c3c3c"
GUESSED_TOKEN = "log-redaction-guessed-bearer-token-9d9d"


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    with conn.transaction(force_rollback=True):
        yield conn


def _logged(*, redacting: bool) -> tuple[io.StringIO, logging.Handler]:
    """The production handler, writing to a buffer; optionally without its filter."""
    configuration = logging_configuration("debug")
    if not redacting:
        handlers = configuration["handlers"]
        assert isinstance(handlers, dict)
        handlers["stderr"].pop("filters")
    logging.config.dictConfig(configuration)
    handler = logging.getLogger().handlers[0]
    buffer = io.StringIO()
    assert isinstance(handler, logging.StreamHandler)
    handler.setStream(buffer)
    return buffer, handler


def _start(
    monkeypatch: pytest.MonkeyPatch, database_url: str, rollback: Any, previous: str
) -> Any:
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv(
        "TOKEN_HASH_PEPPER", PEPPER if not previous else "a-new-" + PEPPER
    )
    monkeypatch.setenv("TOKEN_HASH_PEPPER_PREVIOUS", previous)
    monkeypatch.setenv("METRICS_TOKEN", METRICS_TOKEN)
    monkeypatch.setenv("REGISTRATION_INVITE_TOKEN", BOOTSTRAP_INVITE)
    app = create_app()
    app.dependency_overrides[get_connection] = lambda: rollback
    return app


def _registration(invite: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "invite_token": invite,
        "registration_key": REGISTRATION_KEY,
        "name": "redaction-station",
        "operator": "NTTF NEC",
        "location": {"lat": 12.97, "lon": 77.59, "alt_m": 920},
        "simulated": False,
        "capabilities": [
            {
                "band": "vhf",
                "freq_min_hz": 136000000,
                "freq_max_hz": 138000000,
                "modes": ["lrpt"],
                "polarisation": "rhcp",
                "tracking": False,
                "min_elevation_deg": 10,
            }
        ],
        "client": {"impl": "meridian-reference", "version": "0.1.0"},
    }
    body.update(overrides)
    return body


def _heartbeat(client: TestClient, station_id: str, token: str, **body: Any) -> None:
    client.post(
        "/msp/v0/heartbeat",
        json={
            "station_id": station_id,
            "sent_at": "2026-09-28T09:31:02Z",
            "state": "idle",
            "held_assignments": [],
            "health": {},
            **body,
        },
        headers={**CURRENT, "Authorization": f"Bearer {token}"},
    )


def _exercise(
    monkeypatch: pytest.MonkeyPatch, database_url: str, rollback: Any
) -> list[str]:
    """Drive every path that could log a secret; return the secrets involved."""
    with rollback.cursor() as cur:
        cur.execute(
            "insert into invite_tokens (token_sha256, label) values (%s, %s)",
            (hash_invite_token(STATION_INVITE), "log-redaction"),
        )
    with TestClient(_start(monkeypatch, database_url, rollback, "")) as client:
        # A body missing a field, carrying both secrets: the validation error
        # echoes the whole body as its input, and the platform logs it at info.
        malformed = _registration(STATION_INVITE)
        del malformed["name"]
        client.post("/msp/v0/register", json=malformed, headers=CURRENT)
        registered = client.post(
            "/msp/v0/register", json=_registration(STATION_INVITE), headers=CURRENT
        ).json()
        token, station_id = registered["token"], registered["station_id"]
        # A recovery with the wrong key: refused and logged.
        client.post(
            "/msp/v0/register",
            json=_registration(STATION_INVITE, registration_key="x" * 40),
            headers=CURRENT,
        )
        _heartbeat(client, station_id, token)
        _heartbeat(client, station_id, GUESSED_TOKEN)
        _heartbeat(client, station_id, token, state=["not", "a", "state"])
        client.get("/metrics", headers={"Authorization": "Bearer wrong-metrics-token"})
        client.get("/metrics", headers={"Authorization": f"Bearer {METRICS_TOKEN}"})
        client.get("/api/v1/stations")
        logging.getLogger("meridian.test").info(
            "a stray line with %s and postgresql://meridian:%s@db/x",
            f"Authorization: Bearer {token}",
            "a-database-password-nobody-should-see",
        )
    # Restarted under a rotated pepper: the re-hash on the next heartbeat logs.
    with TestClient(_start(monkeypatch, database_url, rollback, PEPPER)) as client:
        _heartbeat(client, station_id, token)
    return [
        PEPPER,
        "a-new-" + PEPPER,
        METRICS_TOKEN,
        BOOTSTRAP_INVITE,
        STATION_INVITE,
        REGISTRATION_KEY,
        GUESSED_TOKEN,
        token,
        "a-database-password-nobody-should-see",
    ]


@pytest.fixture
def restore_logging() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)
    forget_secrets()


@pytest.mark.usefixtures("restore_logging")
def test_no_secret_appears_in_any_log_line(
    monkeypatch: pytest.MonkeyPatch, database_url: str, rollback: Any
) -> None:
    buffer, _ = _logged(redacting=True)

    secrets = _exercise(monkeypatch, database_url, rollback)

    written = buffer.getvalue()
    assert written.count("\n") > 10, "the flow logged too little to prove anything"
    assert REDACTED in written
    leaked = [secret for secret in secrets if secret in written]
    assert leaked == []


@pytest.mark.usefixtures("restore_logging")
def test_the_same_flow_without_the_filter_does_leak(
    monkeypatch: pytest.MonkeyPatch, database_url: str, rollback: Any
) -> None:
    """The positive control: the search can find a secret when one is there."""
    buffer, _ = _logged(redacting=False)

    secrets = _exercise(monkeypatch, database_url, rollback)

    written = buffer.getvalue()
    leaked = {secret for secret in secrets if secret in written}
    # The two a real request leaks, not only the line this test wrote itself.
    assert {STATION_INVITE, REGISTRATION_KEY} <= leaked


def test_the_reference_client_logs_no_credential_it_holds(
    monkeypatch: pytest.MonkeyPatch,
    database_url: str,
    rollback: Any,
    tmp_path: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The station side, which has no filter: it must simply never log one.

    `caplog` records every logger at debug, the client's, httpx's and the
    platform's, before any handler could redact anything.
    """
    from meridian_client.registration import ReceiveChain, StationProfile, register
    from meridian_client.transport import MspTransport, ProtocolError

    with rollback.cursor() as cur:
        cur.execute(
            "insert into invite_tokens (token_sha256, label) values (%s, %s)",
            (hash_invite_token(STATION_INVITE), "log-redaction-client"),
        )
    profile = StationProfile(
        name="redaction-client",
        operator="meridian",
        lat_deg=12.97,
        lon_deg=77.59,
        alt_m=920.0,
        capabilities=(
            ReceiveChain(
                band="vhf",
                freq_min_hz=136_000_000,
                freq_max_hz=138_000_000,
                modes=("lrpt",),
                polarisation="rhcp",
                tracking=False,
                min_elevation_deg=10.0,
            ),
        ),
    )
    caplog.set_level(logging.DEBUG)
    with TestClient(_start(monkeypatch, database_url, rollback, "")) as started:
        base = "http://platform.test"
        with MspTransport(base, http_transport=started._transport) as anonymous:
            credentials = register(
                anonymous,
                profile,
                invite_token=STATION_INVITE,
                registration_key_path=tmp_path / "registration_key",
            )
        with MspTransport(
            base,
            bearer_token=credentials.bearer_token,
            http_transport=started._transport,
        ) as station:
            station.heartbeat(
                {
                    "station_id": credentials.station_id,
                    "sent_at": "2026-09-28T09:31:02Z",
                    "state": "idle",
                    "held_assignments": [],
                    "health": {},
                }
            )
        with (
            MspTransport(
                base, bearer_token=GUESSED_TOKEN, http_transport=started._transport
            ) as stranger,
            pytest.raises(ProtocolError),
        ):
            stranger.heartbeat({"station_id": credentials.station_id})

    written = "\n".join(record.getMessage() for record in caplog.records)
    held = [
        STATION_INVITE,
        credentials.registration_key,
        credentials.bearer_token,
        GUESSED_TOKEN,
    ]
    assert caplog.records
    assert [secret for secret in held if secret in written] == []
