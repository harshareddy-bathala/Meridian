"""What a heartbeat *does*, as opposed to what it answers.

``tests/msp_conformance/test_heartbeat_endpoint.py`` asserts the wire shape. This
asserts the side effects the route composes, which no store-level test can see:
that both writes happen together, and that ``simulated`` reaches the row from the
registration record rather than from anything the station sent.

Marked ``integration`` by the directory hook in ``tests/conftest.py``.

Reference: docs/MSP-SPEC.md §4.2; docs/DECISIONS.md D-013, D-048, D-054.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from fastapi.testclient import TestClient  # noqa: E402

from meridian.api.app import create_app  # noqa: E402
from meridian.api.dependencies import get_connection  # noqa: E402
from meridian.registry.liveness import derive_liveness  # noqa: E402
from meridian.store.invites import hash_invite_token  # noqa: E402
from meridian.store.stations import find_station_heartbeat  # noqa: E402

pytestmark = pytest.mark.integration

CURRENT = {"MSP-Version": "0.1"}

SATELLITE_ID = "norad:57166"
"""Meteor-M 2-3, the project's primary reception target."""

CENTRE_FREQ_HZ = 137_900_000
"""Its LRPT downlink, the frequency MSP §4.2's own example carries."""

LINE1 = "1 25544U 98067A   26226.50000000  .00001234  00000-0  12345-4 0  9991"
LINE2 = "2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.50377579123456"


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    """Undo everything this test writes."""
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def client(
    database_url: str, rollback: Any, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("TOKEN_HASH_PEPPER", "heartbeat-effects-pepper")
    app = create_app()
    app.dependency_overrides[get_connection] = lambda: rollback
    with TestClient(app) as started:
        yield started
    app.dependency_overrides.clear()


def register(client: TestClient, rollback: Any, *, simulated: bool) -> dict[str, str]:
    """Admit one station, simulated or not, and return its id and token."""
    plaintext = f"effects-invite-{simulated}"
    with rollback.cursor() as cur:
        cur.execute(
            "insert into invite_tokens (token_sha256, label) values (%s, %s)",
            (hash_invite_token(plaintext), "effects"),
        )
    payload: dict[str, Any] = {
        "invite_token": plaintext,
        "registration_key": "a-registration-key",
        "name": "effects-station",
        "operator": "tests",
        "location": {"lat": 12.9716, "lon": 77.5946, "alt_m": 920},
        "simulated": simulated,
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
    }
    if simulated:
        payload["simulator_run_id"] = "run-1"
        payload["seed"] = 4471
    response = client.post("/msp/v0/register", json=payload, headers=CURRENT)
    assert response.status_code == 200, response.text
    body = response.json()
    return {"station_id": body["station_id"], "token": body["token"]}


def send_heartbeat(
    client: TestClient,
    station: dict[str, str],
    *,
    holding: Sequence[str] = (),
    listening_on: str | None = None,
) -> dict[str, Any]:
    """One valid heartbeat from ``station``, stating what it holds.

    Args:
        client: The started test app.
        station: The id and bearer token from :func:`register`.
        holding: MSP §4.2's ``held_assignments``. Empty by default and sent as
            ``[]``, which is a statement rather than an omission.
        listening_on: An assignment to report executing, or ``None`` for no
            block at all.

    Returns:
        The parsed §4.2 response body.
    """
    body: dict[str, Any] = {
        "station_id": station["station_id"],
        "sent_at": "2026-08-14T09:31:02Z",
        "state": "listening" if listening_on else "idle",
        "held_assignments": list(holding),
        "health": {},
    }
    if listening_on is not None:
        body["listening"] = {
            "assignment_id": listening_on,
            "satellite_id": SATELLITE_ID,
            "centre_freq_hz": CENTRE_FREQ_HZ,
            "mode": "lrpt",
        }
    response = client.post(
        "/msp/v0/heartbeat",
        json=body,
        headers={**CURRENT, "Authorization": f"Bearer {station['token']}"},
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


@dataclass(frozen=True, slots=True)
class Issued:
    """One assignment to plant, in the two ways tests here need to vary it.

    A dataclass rather than four keyword arguments, per CLAUDE.local.md §2 — and
    it reads better at the call site, where ``Issued("as_x", ended_hours_ago=1)``
    says what the row is for.
    """

    assignment_id: str
    state: str = "issued"

    ended_hours_ago: float | None = None
    """``None`` puts the window around now; a number puts it wholly in the past,
    which is what makes the row eligible for expiry."""


def issue_assignment(rollback: Any, station_id: str, one: Issued) -> None:
    """Give ``station_id`` one assignment.

    Builds the pass and element set it needs on first call and reuses them
    afterwards, so several assignments in one test share one predicted pass —
    which is also what a real schedule produces for two configurations.
    """
    now = datetime.now(UTC)
    end_at = (
        now + timedelta(minutes=7)
        if one.ended_hours_ago is None
        else now - timedelta(hours=one.ended_hours_ago)
    )
    with rollback.cursor() as cur:
        cur.execute("select id from passes where station_id = %s", (station_id,))
        existing = cur.fetchone()
        pass_id = existing[0] if existing else _build_pass(cur, station_id, now)
        cur.execute(
            "insert into assignments (assignment_id, pass_id, station_id, start_at,"
            " end_at, centre_freq_hz, mode, timing_uncertainty_s, reason, state)"
            " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                one.assignment_id,
                pass_id,
                station_id,
                end_at - timedelta(minutes=10),
                end_at,
                CENTRE_FREQ_HZ,
                "lrpt",
                4.2,
                "reconciliation fixture",
                one.state,
            ),
        )


def _build_pass(cur: Any, station_id: str, now: datetime) -> int:
    """One satellite, one element set and one pass, returning the pass id."""
    cur.execute(
        "insert into satellites (satellite_id, name) values (%s, %s)"
        " on conflict do nothing",
        (SATELLITE_ID, "Reconciliation satellite"),
    )
    cur.execute(
        "insert into element_sets (satellite_id, epoch, line1, line2, source)"
        " values (%s, %s, %s, %s, %s) returning id",
        (SATELLITE_ID, now - timedelta(hours=7), LINE1, LINE2, "manual"),
    )
    (element_set_id,) = cur.fetchone()
    cur.execute(
        "insert into passes (satellite_id, station_id, aos, los, max_elevation_deg,"
        " max_elevation_at, aos_azimuth_deg, los_azimuth_deg, element_set_id,"
        " min_elevation_deg) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        " returning id",
        (
            SATELLITE_ID,
            station_id,
            now - timedelta(minutes=3),
            now + timedelta(minutes=7),
            61.4,
            now + timedelta(minutes=2),
            10.0,
            200.0,
            element_set_id,
            10.0,
        ),
    )
    (pass_id,) = cur.fetchone()
    return int(pass_id)


def state_of(rollback: Any, assignment_id: str) -> str:
    """The stored state of one assignment."""
    with rollback.cursor() as cur:
        cur.execute(
            "select state from assignments where assignment_id = %s", (assignment_id,)
        )
        row = cur.fetchone()
    assert row is not None
    return str(row[0])


def test_a_heartbeat_moves_a_station_from_never_seen_to_online(
    client: TestClient, rollback: Any
) -> None:
    """The whole Stage 5 chain, end to end.

    Registration leaves `last_heartbeat_at` null, which derives as `never_seen`
    — a commissioning problem. One heartbeat later the same station derives as
    `online`. Liveness is not stored (D-054), so this is genuinely reading the
    instant the route wrote rather than a column somebody remembered to update.
    """
    station = register(client, rollback, simulated=False)

    before = find_station_heartbeat(rollback, station["station_id"])
    assert before is not None and before.last_heartbeat_at is None

    send_heartbeat(client, station)

    after = find_station_heartbeat(rollback, station["station_id"])
    assert after is not None and after.last_heartbeat_at is not None
    with rollback.cursor() as cur:
        cur.execute("select now()")
        row = cur.fetchone()
    assert row is not None
    assert derive_liveness(after.last_heartbeat_at, now=row[0]) == "online"


def test_the_heartbeat_row_and_the_station_bump_land_together(
    client: TestClient, rollback: Any
) -> None:
    """Both writes or neither.

    A heartbeat row without the `stations` bump leaves `liveness()` reading
    `offline` for a station that just reported; the bump without the row loses
    the listening evidence `was_listening()` depends on.
    """
    station = register(client, rollback, simulated=False)
    send_heartbeat(client, station)

    with rollback.cursor() as cur:
        cur.execute(
            "select count(*) from heartbeats where station_id = %s",
            (station["station_id"],),
        )
        row = cur.fetchone()
    assert row is not None and row[0] == 1


def test_simulated_is_taken_from_the_registration_not_the_wire(
    client: TestClient, rollback: Any
) -> None:
    """CLAUDE.md rule 5 and D-048, at the one layer that could break them.

    MSP §4.2 puts no `simulated` field on the wire, so the only value the route
    could use is the station's registration record — and this asserts it does.
    A simulated station filing measured-looking heartbeats is the credibility
    failure the rule exists to prevent.
    """
    station = register(client, rollback, simulated=True)
    send_heartbeat(client, station)

    with rollback.cursor() as cur:
        cur.execute(
            "select simulated from heartbeats where station_id = %s",
            (station["station_id"],),
        )
        row = cur.fetchone()
    assert row is not None and row[0] is True


def test_an_assignment_the_station_names_becomes_held(
    client: TestClient, rollback: Any
) -> None:
    """MSP §4.2 row 1, through the endpoint that has to notice it."""
    station = register(client, rollback, simulated=False)
    issue_assignment(rollback, station["station_id"], Issued("as_taken"))

    send_heartbeat(client, station, holding=["as_taken"])

    assert state_of(rollback, "as_taken") == "held"


def test_an_assignment_the_station_omits_stays_issued(
    client: TestClient, rollback: Any
) -> None:
    """MSP §4.2 row 2 under D-022: a decline while the window is open changes nothing.

    The station is offered it again on the next heartbeat. There is no state
    meaning "taken back", so a transition here would misreport the decline.
    """
    station = register(client, rollback, simulated=False)
    issue_assignment(rollback, station["station_id"], Issued("as_declined"))

    send_heartbeat(client, station, holding=[])

    assert state_of(rollback, "as_declined") == "issued"


def test_a_listening_block_starts_the_assignment_and_it_is_still_delivered(
    client: TestClient, rollback: Any
) -> None:
    """The two halves of D-067's second finding, in one heartbeat.

    Reporting `listening` moves the row to `in_progress`, and the response still
    carries it — because a station that reboots while receiving must be told what
    it is in the middle of, not that it has nothing to do.
    """
    station = register(client, rollback, simulated=False)
    issue_assignment(
        rollback, station["station_id"], Issued("as_running", state="held")
    )

    body = send_heartbeat(
        client, station, holding=["as_running"], listening_on="as_running"
    )

    assert state_of(rollback, "as_running") == "in_progress"
    assert [one["assignment_id"] for one in body["assignments"]] == ["as_running"]


def test_a_station_may_confirm_and_begin_in_one_heartbeat(
    client: TestClient, rollback: Any
) -> None:
    """`issued -> held -> in_progress` from a single message.

    Which is why the route applies the holds before the start: a pass that opened
    between two heartbeats has genuinely done both, and deferring the second
    would delay `in_progress` by a whole 30-second interval.
    """
    station = register(client, rollback, simulated=False)
    issue_assignment(rollback, station["station_id"], Issued("as_both"))

    send_heartbeat(client, station, holding=["as_both"], listening_on="as_both")

    assert state_of(rollback, "as_both") == "in_progress"


def test_an_assignment_never_issued_to_this_station_changes_nothing(
    client: TestClient, rollback: Any
) -> None:
    """MSP §4.2 row 4: log and ignore.

    The heartbeat is still accepted — a station with one bad id in its list is
    still reporting liveness, and refusing the whole message would lose the
    listening evidence in it.
    """
    station = register(client, rollback, simulated=False)
    issue_assignment(rollback, station["station_id"], Issued("as_real"))

    send_heartbeat(client, station, holding=["as_real", "as_invented"])

    assert state_of(rollback, "as_real") == "held"

    with rollback.cursor() as cur:
        cur.execute(
            "select count(*) from assignments where assignment_id = %s",
            ("as_invented",),
        )
        row = cur.fetchone()
    assert row is not None and row[0] == 0


def test_the_station_s_own_list_is_what_exempts_a_row_from_expiry(
    client: TestClient, rollback: Any
) -> None:
    """D-067, at the layer that has to pass the list along.

    Two assignments, both an hour past their window. The station still names one
    and has stopped naming the other. Only the dropped one expires — which is
    only true if the route hands `expire_overdue_assignments` the heartbeat's
    actual `held_assignments` rather than an empty list.

    The surviving row matters more than the expiring one: it is a station that
    executed a pass and has an observation still to submit, and D-008 has no arc
    from `expired` back to `reported` for it to land on.
    """
    station = register(client, rollback, simulated=False)
    issue_assignment(
        rollback,
        station["station_id"],
        Issued("as_finishing", state="held", ended_hours_ago=1),
    )
    issue_assignment(
        rollback,
        station["station_id"],
        Issued("as_abandoned", state="held", ended_hours_ago=1),
    )

    send_heartbeat(client, station, holding=["as_finishing"])

    assert state_of(rollback, "as_finishing") == "held"
    assert state_of(rollback, "as_abandoned") == "expired"
