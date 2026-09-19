"""``POST /msp/v0/observations`` on the wire, asserted against the specification.

Conformance, not integration: these assert the **bytes** MSP §4.4 promises — the
three-field acknowledgement, the error codes, and which bodies are refused.
The station is registered through ``POST /msp/v0/register`` so the bearer token
under test is one the platform actually minted.

Marked ``msp_conformance`` by the directory hook in ``tests/conftest.py``.

Reference: docs/MSP-SPEC.md §4.4, §6, §8; docs/DECISIONS.md D-013, D-027, D-032,
D-072.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from meridian.api.app import create_app
from meridian.api.dependencies import get_connection
from meridian.store.invites import hash_invite_token

OBSERVATIONS_PATH = "/msp/v0/observations"
REGISTER_PATH = "/msp/v0/register"
CURRENT = {"MSP-Version": "0.1"}

SATELLITE_ID = "norad:57166"
LINE1 = "1 25544U 98067A   26226.50000000  .00001234  00000-0  12345-4 0  9991"
LINE2 = "2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.50377579123456"


def recent(**offset: float) -> datetime:
    """An instant near now, so D-013's ingest window is satisfied by default."""
    return datetime.now(UTC) - timedelta(hours=1) + timedelta(**offset)


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    """Undo everything this test writes — see test_heartbeat_endpoint.py's twin."""
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def client(
    database_url: str, rollback: Any, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    """A client whose writes are rolled back, sharing this test's connection."""
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("TOKEN_HASH_PEPPER", "observations-conformance-pepper")
    app = create_app()
    app.dependency_overrides[get_connection] = lambda: rollback
    with TestClient(app) as started:
        yield started
    app.dependency_overrides.clear()


def register_station(client: TestClient, rollback: Any, label: str) -> dict[str, str]:
    """Mint one station through the real endpoint and return its credentials."""
    with rollback.cursor() as cur:
        cur.execute(
            "insert into invite_tokens (token_sha256, label) values (%s, %s)",
            (hash_invite_token(f"invite-{label}"), f"observations-{label}"),
        )
    response = client.post(
        REGISTER_PATH,
        json={
            "invite_token": f"invite-{label}",
            "registration_key": f"key-{label}",
            "name": f"nec-rooftop-{label}",
            "operator": "NTTF NEC",
            "location": {"lat": 12.9716, "lon": 77.5946, "alt_m": 920},
            "simulated": False,
            "capabilities": [
                {
                    "band": "vhf",
                    "freq_min_hz": 136000000,
                    "freq_max_hz": 138000000,
                    "modes": ["lrpt"],
                    "polarisation": "rhcp",
                    "tracking": True,
                    "min_elevation_deg": 10,
                }
            ],
            "client": {"impl": "meridian-reference", "version": "0.1.0"},
        },
        headers=CURRENT,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    return {"station_id": body["station_id"], "token": body["token"]}


@pytest.fixture
def station(client: TestClient, rollback: Any) -> dict[str, str]:
    """The station under test, with one assignment it may report on."""
    registered = register_station(client, rollback, "primary")
    issue_assignment(rollback, registered["station_id"], "as_44b2")
    return registered


def issue_assignment(
    rollback: Any, station_id: str, assignment_id: str, state: str = "in_progress"
) -> None:
    """The row graph an assignment needs, then the assignment itself."""
    with rollback.cursor() as cur:
        cur.execute(
            "insert into satellites (satellite_id, name) values (%s, %s)"
            " on conflict do nothing",
            (SATELLITE_ID, "Meteor-M N2-4"),
        )
        cur.execute(
            "insert into element_sets (satellite_id, epoch, line1, line2, source)"
            " values (%s, %s, %s, %s, %s)"
            " on conflict on constraint element_set_content_unique do nothing",
            (SATELLITE_ID, recent(hours=-24), LINE1, LINE2, "manual"),
        )
        cur.execute(
            "select id from element_sets where satellite_id = %s limit 1",
            (SATELLITE_ID,),
        )
        (element_set_id,) = cur.fetchone()
        cur.execute(
            "insert into passes (satellite_id, station_id, aos, los,"
            " max_elevation_deg, max_elevation_at, aos_azimuth_deg, los_azimuth_deg,"
            " element_set_id, min_elevation_deg)"
            " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id",
            (
                SATELLITE_ID,
                station_id,
                recent(),
                recent(minutes=11),
                61.4,
                recent(minutes=5),
                10.0,
                200.0,
                element_set_id,
                10.0,
            ),
        )
        (pass_id,) = cur.fetchone()
        cur.execute(
            "insert into assignments (assignment_id, pass_id, station_id, start_at,"
            " end_at, centre_freq_hz, mode, timing_uncertainty_s, reason, state)"
            " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                assignment_id,
                pass_id,
                station_id,
                recent(),
                recent(minutes=11),
                137900000,
                "lrpt",
                4.2,
                "conformance fixture",
                state,
            ),
        )


def observation_body(station_id: str, **overrides: Any) -> dict[str, Any]:
    """MSP §4.4's example payload, with the fields a test varies exposed."""
    body: dict[str, Any] = {
        "assignment_id": "as_44b2",
        "station_id": station_id,
        "started_at": recent().isoformat().replace("+00:00", "Z"),
        "ended_at": recent(minutes=11).isoformat().replace("+00:00", "Z"),
        "outcome": "decoded",
        "signal": {
            "detected": True,
            "first_detection_at": recent(seconds=35).isoformat().replace("+00:00", "Z"),
            "peak_snr_db": 11.4,
            "doppler_samples": [
                {
                    "t": recent(seconds=35).isoformat().replace("+00:00", "Z"),
                    "offset_hz": 3140,
                },
            ],
        },
        "products": [
            {"kind": "image", "uri": "file:///x.png", "sha256": "aa", "frames": 412}
        ],
        "client_notes": "rotator lagged 2s at AOS",
    }
    body.update(overrides)
    return body


def auth(station: dict[str, str]) -> dict[str, str]:
    """The headers an observation carries: version and bearer token."""
    return {**CURRENT, "Authorization": f"Bearer {station['token']}"}


def test_the_acknowledgement_has_exactly_the_three_fields_msp_defines(
    client: TestClient, station: dict[str, str]
) -> None:
    """MSP §4.4: `observation_id`, `assignment_id`, `superseded`, and no others."""
    response = client.post(
        OBSERVATIONS_PATH,
        json=observation_body(station["station_id"]),
        headers=auth(station),
    )

    assert response.status_code == 200, response.text
    assert sorted(response.json()) == ["assignment_id", "observation_id", "superseded"]


def test_the_observation_id_is_the_one_the_specification_publishes(
    client: TestClient, station: dict[str, str]
) -> None:
    """MSP §4.4 states `ob_05601bd09768` is the real id for `as_44b2` revision 1.

    The specification calls it regenerable, so this asserts the published value
    literally rather than recomputing it — a test that derived the expectation
    with the same formula under test would pass with both wrong together.
    """
    response = client.post(
        OBSERVATIONS_PATH,
        json=observation_body(station["station_id"]),
        headers=auth(station),
    )

    assert response.json()["observation_id"] == "ob_05601bd09768"


def test_the_published_id_is_what_the_formula_produces() -> None:
    """D-027's derivation, checked against the specification's own example."""
    digest = hashlib.sha256(b"as_44b2:1").hexdigest()

    assert f"ob_{digest[:12]}" == "ob_05601bd09768"


def test_an_identical_resubmission_returns_the_identical_acknowledgement(
    client: TestClient, station: dict[str, str]
) -> None:
    """MSP §4.4: "a resubmission that changes nothing returns the identical id".

    This is the queued-retry case of §6 — the station never saw the first
    answer — and it must be idempotent all the way out to the acknowledgement.
    """
    body = observation_body(station["station_id"])

    first = client.post(OBSERVATIONS_PATH, json=body, headers=auth(station))
    second = client.post(OBSERVATIONS_PATH, json=body, headers=auth(station))

    assert first.json() == second.json()
    assert second.json()["superseded"] is False


def test_a_changed_resubmission_reports_superseded(
    client: TestClient, station: dict[str, str]
) -> None:
    """MSP §4.4: `superseded` is true when this submission replaced the current one."""
    station_id = station["station_id"]
    client.post(
        OBSERVATIONS_PATH, json=observation_body(station_id), headers=auth(station)
    )

    corrected = observation_body(
        station_id,
        outcome="signal_no_decode",
        signal={
            "detected": True,
            "first_detection_at": recent(seconds=35).isoformat().replace("+00:00", "Z"),
            "peak_snr_db": 4.0,
        },
    )
    response = client.post(OBSERVATIONS_PATH, json=corrected, headers=auth(station))

    assert response.status_code == 200, response.text
    assert response.json()["superseded"] is True
    assert response.json()["observation_id"] != "ob_05601bd09768"


def test_an_unknown_assignment_is_404_unknown_assignment(
    client: TestClient, station: dict[str, str]
) -> None:
    """MSP §6's code table, exactly."""
    response = client.post(
        OBSERVATIONS_PATH,
        json=observation_body(station["station_id"], assignment_id="as_nope"),
        headers=auth(station),
    )

    assert response.status_code == 404
    assert response.json()["error"] == "unknown_assignment"


@pytest.mark.usefixtures("station")
def test_another_stations_assignment_is_403_not_owner(
    client: TestClient, rollback: Any
) -> None:
    """MSP §3: "a station may only submit observations for assignments issued to it".

    The `station` fixture is required for its effect rather than its value: it is
    what puts `as_44b2` in the database, owned by someone other than the intruder.
    """
    intruder = register_station(client, rollback, "intruder")

    response = client.post(
        OBSERVATIONS_PATH,
        json=observation_body(intruder["station_id"]),
        headers=auth(intruder),
    )

    assert response.status_code == 403
    assert response.json()["error"] == "not_owner"


def test_a_body_naming_another_station_is_403_not_owner(
    client: TestClient, station: dict[str, str]
) -> None:
    """The token is the authoritative identity; a disagreement is refused."""
    response = client.post(
        OBSERVATIONS_PATH,
        json=observation_body("st_someone_else"),
        headers=auth(station),
    )

    assert response.status_code == 403
    assert response.json()["error"] == "not_owner"


def test_it_requires_authentication(
    client: TestClient, station: dict[str, str]
) -> None:
    """MSP §3: `observations` requires an `Authorization` header."""
    response = client.post(
        OBSERVATIONS_PATH, json=observation_body(station["station_id"]), headers=CURRENT
    )

    assert response.status_code == 401
    assert response.json()["error"] == "unauthorized"


def test_a_missing_version_is_unsupported_version(
    client: TestClient, station: dict[str, str]
) -> None:
    """MSP §7 applies to every endpoint, this one included."""
    response = client.post(
        OBSERVATIONS_PATH,
        json=observation_body(station["station_id"]),
        headers={"Authorization": f"Bearer {station['token']}"},
    )

    assert response.status_code == 400
    assert response.json()["error"] == "unsupported_version"


def test_more_than_512_doppler_samples_is_malformed(
    client: TestClient, station: dict[str, str]
) -> None:
    """MSP §6's table: "at most 512 samples; more is `malformed`" (D-032)."""
    sample = {
        "t": recent(seconds=35).isoformat().replace("+00:00", "Z"),
        "offset_hz": 3140,
    }
    response = client.post(
        OBSERVATIONS_PATH,
        json=observation_body(
            station["station_id"],
            signal={
                "detected": True,
                "first_detection_at": sample["t"],
                "doppler_samples": [sample] * 513,
            },
        ),
        headers=auth(station),
    )

    assert response.status_code == 400
    assert response.json()["error"] == "malformed"


def test_exactly_512_doppler_samples_is_accepted(
    client: TestClient, station: dict[str, str]
) -> None:
    """The cap is inclusive — 512 is the limit, not the first value refused."""
    sample = {
        "t": recent(seconds=35).isoformat().replace("+00:00", "Z"),
        "offset_hz": 3140,
    }
    response = client.post(
        OBSERVATIONS_PATH,
        json=observation_body(
            station["station_id"],
            signal={
                "detected": True,
                "first_detection_at": sample["t"],
                "doppler_samples": [sample] * 512,
            },
        ),
        headers=auth(station),
    )

    assert response.status_code == 200, response.text


def test_a_started_at_older_than_thirty_days_is_malformed(
    client: TestClient, station: dict[str, str]
) -> None:
    """D-013: `started_at` is the partitioning column and cannot be trusted blindly.

    A station with a dead clock would otherwise place a chunk in 1970 that every
    retention and compression policy then mishandles forever.
    """
    long_ago = (datetime.now(UTC) - timedelta(days=40)).isoformat()
    response = client.post(
        OBSERVATIONS_PATH,
        json=observation_body(
            station["station_id"], started_at=long_ago, ended_at=long_ago
        ),
        headers=auth(station),
    )

    assert response.status_code == 400
    assert response.json()["error"] == "malformed"


def test_a_started_at_in_the_future_is_malformed(
    client: TestClient, station: dict[str, str]
) -> None:
    """A pass cannot begin after the report of it arrives."""
    ahead = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    response = client.post(
        OBSERVATIONS_PATH,
        json=observation_body(station["station_id"], started_at=ahead, ended_at=ahead),
        headers=auth(station),
    )

    assert response.status_code == 400
    assert response.json()["error"] == "malformed"


def test_a_window_that_runs_backwards_is_malformed(
    client: TestClient, station: dict[str, str]
) -> None:
    """`started_at` after `ended_at` is not clock skew, it is a bug."""
    response = client.post(
        OBSERVATIONS_PATH,
        json=observation_body(
            station["station_id"],
            started_at=recent(minutes=11).isoformat().replace("+00:00", "Z"),
            ended_at=recent().isoformat().replace("+00:00", "Z"),
        ),
        headers=auth(station),
    )

    assert response.status_code == 400
    assert response.json()["error"] == "malformed"


@pytest.mark.parametrize("outcome", ["decoded", "signal_no_decode"])
def test_an_outcome_claiming_a_signal_needs_one(
    client: TestClient, station: dict[str, str], outcome: str
) -> None:
    """D-072: `decoded` without a detection contradicts itself."""
    response = client.post(
        OBSERVATIONS_PATH,
        json=observation_body(
            station["station_id"], outcome=outcome, signal={"detected": False}
        ),
        headers=auth(station),
    )

    assert response.status_code == 400
    assert response.json()["error"] == "malformed"


@pytest.mark.parametrize("outcome", ["no_signal", "not_attempted"])
def test_an_outcome_denying_a_signal_cannot_carry_one(
    client: TestClient, station: dict[str, str], outcome: str
) -> None:
    """MSP §4.4: `no_signal` is a station that heard nothing; `not_attempted` never
    began. Neither can carry a detection."""
    response = client.post(
        OBSERVATIONS_PATH,
        json=observation_body(station["station_id"], outcome=outcome),
        headers=auth(station),
    )

    assert response.status_code == 400
    assert response.json()["error"] == "malformed"


def test_a_no_signal_report_is_accepted_and_is_data(
    client: TestClient, station: dict[str, str]
) -> None:
    """A station that listened and heard nothing has measured something.

    This is the row CLAUDE.md rule 7 depends on existing: without it, absence
    cannot be told from a station that was never listening.
    """
    response = client.post(
        OBSERVATIONS_PATH,
        json=observation_body(
            station["station_id"],
            outcome="no_signal",
            signal={"detected": False},
            products=[],
        ),
        headers=auth(station),
    )

    assert response.status_code == 200, response.text


def test_a_non_finite_number_inside_products_is_malformed(
    client: TestClient, station: dict[str, str]
) -> None:
    """D-072 refuses a non-finite float *anywhere*, and `products` is anywhere.

    Sent as raw bytes because that is the only way one arrives: no station
    writes `Infinity`, but `1e400` is an ordinary-looking number that every JSON
    parser turns into one. Left unchecked it survives validation and fails later
    — at the digest, which cannot render it, and at the `jsonb` cast, which
    cannot store it — so the station is told the platform broke when its own
    body did, and a 500 is retriable, so it resubmits the same body forever.
    """
    body = observation_body(
        station["station_id"], products=[{"kind": "image", "score": 0}]
    )
    raw = json.dumps(body).replace('"score": 0', '"score": 1e400')

    response = client.post(
        OBSERVATIONS_PATH,
        content=raw,
        headers={**auth(station), "Content-Type": "application/json"},
    )

    assert response.status_code == 400, response.text
    assert response.json()["error"] == "malformed"


def test_an_unknown_outcome_is_malformed(
    client: TestClient, station: dict[str, str]
) -> None:
    """The enum is MSP §4.4's five values and nothing else (D-010)."""
    response = client.post(
        OBSERVATIONS_PATH,
        json=observation_body(station["station_id"], outcome="partially_decoded"),
        headers=auth(station),
    )

    assert response.status_code == 400
    assert response.json()["error"] == "malformed"


def test_an_error_body_has_exactly_two_flat_string_fields(
    client: TestClient, station: dict[str, str]
) -> None:
    """D-004's shape holds on this endpoint too."""
    body = client.post(
        OBSERVATIONS_PATH,
        json=observation_body(station["station_id"], assignment_id="as_nope"),
        headers=auth(station),
    ).json()

    assert sorted(body) == ["error", "message"]
    assert all(isinstance(value, str) for value in body.values())


def test_no_error_response_echoes_the_bearer_token(
    client: TestClient, station: dict[str, str]
) -> None:
    """A rejected credential must never come back in the body."""
    response = client.post(
        OBSERVATIONS_PATH,
        json=observation_body(station["station_id"], assignment_id="as_nope"),
        headers=auth(station),
    )

    assert station["token"] not in response.text


def test_an_accepted_observation_is_counted_by_outcome_and_provenance(
    client: TestClient, station: dict[str, str]
) -> None:
    """Counted with the outcome sent and ``simulated`` from the registration.

    The station here registered as measured, so the label must say ``false``
    whatever the body contains (D-048, D-111).
    """
    labels = {"outcome": "decoded", "simulated": "false"}
    before = REGISTRY.get_sample_value("meridian_msp_observations_total", labels)
    delay_labels = {"simulated": "false"}
    delays_before = REGISTRY.get_sample_value(
        "meridian_observation_submission_delay_seconds_count", delay_labels
    )

    response = client.post(
        OBSERVATIONS_PATH,
        json=observation_body(station["station_id"]),
        headers=auth(station),
    )

    assert response.status_code == 200, response.text
    after = REGISTRY.get_sample_value("meridian_msp_observations_total", labels)
    assert after == (before or 0.0) + 1
    delays_after = REGISTRY.get_sample_value(
        "meridian_observation_submission_delay_seconds_count", delay_labels
    )
    assert delays_after == (delays_before or 0.0) + 1


# --- MSP 0.3: reception evidence (D-103, D-117) -------------------------------

VERSION_03 = {"MSP-Version": "0.3"}


def evidence_auth(station: dict[str, str]) -> dict[str, str]:
    """Headers for a 0.3 station: the new minor, and its bearer token."""
    return {**VERSION_03, "Authorization": f"Bearer {station['token']}"}


def wire_time(**offset: float) -> str:
    return recent(**offset).isoformat().replace("+00:00", "Z")


def evidence_body(station_id: str, **overrides: Any) -> dict[str, Any]:
    """MSP §4.4's 0.3 example: a decoded pass with every evidence field."""
    body = observation_body(station_id)
    body["signal"] = {
        **body["signal"],
        "noise_floor_dbfs": -52.3,
        "receiver_gain_db": 32.8,
        "snr_samples": [
            {"t": wire_time(seconds=35), "snr_db": 3.1},
            {"t": wire_time(minutes=5), "snr_db": 11.4},
        ],
    }
    body["decode"] = {
        "decoder": "satdump",
        "decoder_version": "1.2.2",
        "frames_decoded": 412,
        "frames_failed": 37,
    }
    body.update(overrides)
    return body


def stored_evidence(rollback: Any, assignment_id: str = "as_44b2") -> tuple[Any, ...]:
    with rollback.cursor() as cur:
        cur.execute(
            "select noise_floor_dbfs, receiver_gain_db, snr_samples, decoder,"
            " decoder_version, frames_decoded, frames_failed"
            " from observations_current where assignment_id = %s",
            (assignment_id,),
        )
        row = cur.fetchone()
    assert row is not None
    return tuple(row)


def assert_malformed(response: Any) -> None:
    assert response.status_code == 400, response.text
    assert response.json()["error"] == "malformed"


def test_a_03_observation_stores_its_reception_evidence(
    client: TestClient, station: dict[str, str], rollback: Any
) -> None:
    """Every 0.3 field reaches its column, in the submitted order and shape."""
    response = client.post(
        OBSERVATIONS_PATH,
        json=evidence_body(station["station_id"]),
        headers=evidence_auth(station),
    )

    assert response.status_code == 200, response.text
    floor, gain, samples, decoder, version, decoded, failed = stored_evidence(rollback)
    assert (floor, gain) == (-52.3, 32.8)
    assert [one["snr_db"] for one in samples] == [3.1, 11.4]
    assert (decoder, version, decoded, failed) == ("satdump", "1.2.2", 412, 37)


def test_a_02_observation_is_stored_with_no_evidence_at_all(
    client: TestClient, station: dict[str, str], rollback: Any
) -> None:
    """D-117: 0.3 refuses nothing a 0.2 station sends, and invents nothing for it."""
    response = client.post(
        OBSERVATIONS_PATH,
        json=observation_body(station["station_id"]),
        headers=auth(station),
    )

    assert response.status_code == 200, response.text
    assert stored_evidence(rollback) == (None,) * 7


def test_evidence_added_to_an_earlier_02_report_supersedes_it(
    client: TestClient, station: dict[str, str]
) -> None:
    """A station that upgrades and re-reports has corrected, not repeated, itself.

    The two bodies agree on every 0.2 field, so this also proves the evidence is
    part of the content digest (D-118): without it the second submission would
    be taken as an unchanged retry and silently dropped.
    """
    plain = observation_body(station["station_id"])
    first = client.post(OBSERVATIONS_PATH, json=plain, headers=auth(station))

    enriched = evidence_body(station["station_id"])
    enriched["started_at"] = plain["started_at"]
    enriched["ended_at"] = plain["ended_at"]
    enriched["signal"]["first_detection_at"] = plain["signal"]["first_detection_at"]
    enriched["signal"]["doppler_samples"] = plain["signal"]["doppler_samples"]
    second = client.post(
        OBSERVATIONS_PATH, json=enriched, headers=evidence_auth(station)
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert second.json()["superseded"] is True


def test_an_identical_03_resubmission_returns_the_identical_acknowledgement(
    client: TestClient, station: dict[str, str]
) -> None:
    """The queued-retry case of §6, carrying evidence: still idempotent."""
    body = evidence_body(station["station_id"])

    first = client.post(OBSERVATIONS_PATH, json=body, headers=evidence_auth(station))
    second = client.post(OBSERVATIONS_PATH, json=body, headers=evidence_auth(station))

    assert first.json() == second.json()
    assert second.json()["superseded"] is False


def test_more_than_512_snr_samples_is_malformed(
    client: TestClient, station: dict[str, str]
) -> None:
    """MSP §6's table: the same cap as `doppler_samples` (D-117)."""
    body = evidence_body(station["station_id"])
    sample = {"t": wire_time(seconds=35), "snr_db": 3.1}
    body["signal"]["snr_samples"] = [sample] * 513

    assert_malformed(
        client.post(OBSERVATIONS_PATH, json=body, headers=evidence_auth(station))
    )


def test_exactly_512_snr_samples_is_accepted(
    client: TestClient, station: dict[str, str]
) -> None:
    body = evidence_body(station["station_id"])
    sample = {"t": wire_time(seconds=35), "snr_db": 3.1}
    body["signal"]["snr_samples"] = [sample] * 512

    response = client.post(OBSERVATIONS_PATH, json=body, headers=evidence_auth(station))

    assert response.status_code == 200, response.text


def test_a_noise_floor_without_its_gain_is_malformed(
    client: TestClient, station: dict[str, str]
) -> None:
    """D-117: a floor comparable with nothing is not a measurement."""
    body = evidence_body(station["station_id"])
    del body["signal"]["receiver_gain_db"]

    assert_malformed(
        client.post(OBSERVATIONS_PATH, json=body, headers=evidence_auth(station))
    )


def test_a_gain_without_a_noise_floor_is_accepted(
    client: TestClient, station: dict[str, str]
) -> None:
    """The gain describes the receiver on its own; only the reverse is refused."""
    body = evidence_body(station["station_id"])
    del body["signal"]["noise_floor_dbfs"]

    response = client.post(OBSERVATIONS_PATH, json=body, headers=evidence_auth(station))

    assert response.status_code == 200, response.text


@pytest.mark.parametrize(
    "decode",
    [
        {"frames_decoded": 412},
        {"decoder": "", "frames_decoded": 412},
        {"decoder": "satdump", "frames_decoded": -1},
        {"decoder": "satdump", "frames_decoded": True},
        {"decoder": "satdump", "frames_decoded": 412.5},
        {"decoder": "satdump", "frames_failed": -3},
    ],
    ids=[
        "unnamed",
        "empty-name",
        "negative",
        "boolean",
        "fractional",
        "failed-negative",
    ],
)
def test_a_decode_block_that_is_not_a_count_by_a_named_decoder_is_malformed(
    client: TestClient, station: dict[str, str], decode: dict[str, Any]
) -> None:
    """D-117: statistics name their decoder, and counts are whole and non-negative."""
    body = evidence_body(station["station_id"], decode=decode)

    assert_malformed(
        client.post(OBSERVATIONS_PATH, json=body, headers=evidence_auth(station))
    )


def test_a_decode_block_with_only_a_decoder_name_is_accepted(
    client: TestClient, station: dict[str, str], rollback: Any
) -> None:
    """A decoder with no frame structure sends no counts, and none are invented."""
    body = evidence_body(station["station_id"], decode={"decoder": "gr-satellites"})

    response = client.post(OBSERVATIONS_PATH, json=body, headers=evidence_auth(station))

    assert response.status_code == 200, response.text
    assert stored_evidence(rollback)[3:] == ("gr-satellites", None, None, None)


@pytest.mark.parametrize(
    ("outcome", "frames", "detected"),
    [("decoded", 0, True), ("signal_no_decode", 5, True), ("no_signal", 1, False)],
)
def test_frames_that_contradict_the_outcome_are_malformed(
    client: TestClient,
    station: dict[str, str],
    outcome: str,
    frames: int,
    detected: bool,
) -> None:
    """D-117: decoded needs a frame; signal_no_decode and no_signal need none."""
    signal: dict[str, Any] = {"detected": detected}
    if detected:
        signal["first_detection_at"] = wire_time(seconds=35)
    body = observation_body(
        station["station_id"],
        outcome=outcome,
        signal=signal,
        decode={"decoder": "satdump", "frames_decoded": frames},
    )

    assert_malformed(
        client.post(OBSERVATIONS_PATH, json=body, headers=evidence_auth(station))
    )


def test_an_aborted_decode_may_report_any_frame_count(
    client: TestClient, station: dict[str, str]
) -> None:
    """A decode can stop part-way, and what it recovered before stopping is data.

    This is also D-122's report for a decoder that produced frames but no timing:
    `aborted`, with the decode block and no signal block.
    """
    body = observation_body(
        station["station_id"],
        outcome="aborted",
        decode={"decoder": "satdump", "frames_decoded": 120, "frames_failed": 8},
    )
    del body["signal"]

    response = client.post(OBSERVATIONS_PATH, json=body, headers=evidence_auth(station))

    assert response.status_code == 200, response.text


@pytest.mark.parametrize(
    ("signal", "decode"),
    [
        ({"detected": False, "receiver_gain_db": 30.0}, None),
        ({"detected": False, "snr_samples": []}, None),
        (None, {"decoder": "satdump"}),
    ],
    ids=["gain", "snr-samples", "decode-block"],
)
def test_a_not_attempted_report_carrying_evidence_is_malformed(
    client: TestClient,
    station: dict[str, str],
    signal: dict[str, Any] | None,
    decode: dict[str, Any] | None,
) -> None:
    """A station that never began measured nothing, and cannot claim otherwise."""
    body = observation_body(station["station_id"], outcome="not_attempted", products=[])
    del body["signal"]
    if signal is not None:
        body["signal"] = signal
    if decode is not None:
        body["decode"] = decode

    assert_malformed(
        client.post(OBSERVATIONS_PATH, json=body, headers=evidence_auth(station))
    )


def test_a_non_finite_snr_sample_is_malformed(
    client: TestClient, station: dict[str, str]
) -> None:
    """Sent as raw bytes, as `1e400` arrives, for the reason the products test gives."""
    body = evidence_body(station["station_id"])
    raw = json.dumps(body).replace('"snr_db": 11.4', '"snr_db": 1e400')

    assert_malformed(
        client.post(
            OBSERVATIONS_PATH,
            content=raw,
            headers={**evidence_auth(station), "Content-Type": "application/json"},
        )
    )
