"""``meridian.store.heartbeats`` against real TimescaleDB.

Marked ``integration`` by the directory hook in ``tests/conftest.py``. Same
``rollback`` fixture pattern as ``test_store_stations.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from meridian.store.heartbeats import (  # noqa: E402 — after importorskip
    ListeningReport,
    NewHeartbeat,
    insert_heartbeat,
    touch_last_heartbeat,
)
from meridian.store.stations import Capability, NewStation, insert_station  # noqa: E402

pytestmark = pytest.mark.integration

SAMPLE_CAPABILITY = Capability(
    band="vhf",
    freq_min_hz=136_000_000,
    freq_max_hz=138_000_000,
    modes=("lrpt",),
    polarisation="rhcp",
    tracking=True,
    min_elevation_deg=10.0,
)


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    """Undo everything this test writes — see test_store_invites.py's twin."""
    with conn.transaction(force_rollback=True):
        yield conn


def sample_station(station_id: str) -> NewStation:
    return NewStation(
        station_id=station_id,
        name="Test station",
        operator="tests",
        lat_deg=12.9716,
        lon_deg=77.5946,
        alt_m=920.0,
        token_sha256=bytes(32),
        registration_key_sha256=bytes(32),
        simulated=False,
        simulator_run_id=None,
        seed=None,
        client_impl="meridian-reference",
        client_version="0.1.0",
    )


def new_heartbeat(station_id: str, **overrides: Any) -> NewHeartbeat:
    defaults: dict[str, Any] = {
        "station_id": station_id,
        "sent_at": datetime(2026, 8, 14, 9, 31, 2, tzinfo=UTC),
        "state": "listening",
        "held_assignments": ("as_1", "as_2"),
        "listening": None,
        "health_json": "{}",
        "simulated": False,
        "clock_offset_s": 0.184,
        "clock_uncertainty_s": 0.05,
    }
    defaults.update(overrides)
    return NewHeartbeat(**defaults)


def test_insert_heartbeat_round_trips_without_a_listening_block(rollback: Any) -> None:
    insert_station(rollback, sample_station("st_hb1"), [SAMPLE_CAPABILITY])
    insert_heartbeat(rollback, new_heartbeat("st_hb1"))

    with rollback.cursor() as cur:
        cur.execute(
            "select state, held_assignments, listening_assignment_id,"
            " clock_offset_s, clock_uncertainty_s"
            " from heartbeats where station_id = %s",
            ("st_hb1",),
        )
        row = cur.fetchone()
    assert row == ("listening", ["as_1", "as_2"], None, 0.184, 0.05)


def test_insert_heartbeat_round_trips_a_full_listening_block(rollback: Any) -> None:
    insert_station(rollback, sample_station("st_hb2"), [SAMPLE_CAPABILITY])
    listening = ListeningReport(
        assignment_id="as_1",
        satellite_id="norad:57166",
        centre_freq_hz=137900000,
        mode="lrpt",
    )
    insert_heartbeat(rollback, new_heartbeat("st_hb2", listening=listening))

    with rollback.cursor() as cur:
        cur.execute(
            "select listening_assignment_id, listening_satellite_id,"
            " listening_freq_hz, listening_mode"
            " from heartbeats where station_id = %s",
            ("st_hb2",),
        )
        row = cur.fetchone()
    assert row == ("as_1", "norad:57166", 137900000, "lrpt")


def test_insert_heartbeat_stores_health_json(rollback: Any) -> None:
    insert_station(rollback, sample_station("st_hb3"), [SAMPLE_CAPABILITY])
    insert_heartbeat(
        rollback, new_heartbeat("st_hb3", health_json='{"uptime_s": 84213}')
    )

    with rollback.cursor() as cur:
        cur.execute(
            "select health_json from heartbeats where station_id = %s", ("st_hb3",)
        )
        (health,) = cur.fetchone()
    assert health == {"uptime_s": 84213}


def test_a_partial_listening_block_is_rejected_at_the_db(rollback: Any) -> None:
    """Belt and braces: the ``CHECK`` constraint exists independently of
    :class:`ListeningReport`'s all-or-nothing shape. If this ever raised
    something other than ``CheckViolation``, the type would not be the only
    thing protecting the invariant."""
    insert_station(rollback, sample_station("st_hb4"), [SAMPLE_CAPABILITY])
    with pytest.raises(psycopg.errors.CheckViolation), rollback.cursor() as cur:
        cur.execute(
            "insert into heartbeats (station_id, sent_at, state,"
            " listening_assignment_id)"
            " values (%s, now(), %s, %s)",
            ("st_hb4", "listening", "as_1"),
        )


def test_touch_last_heartbeat_updates_the_timestamp(rollback: Any) -> None:
    insert_station(rollback, sample_station("st_hb5"), [SAMPLE_CAPABILITY])
    with rollback.cursor() as cur:
        cur.execute(
            "select last_heartbeat_at from stations where station_id = %s", ("st_hb5",)
        )
        (before,) = cur.fetchone()
    assert before is None

    touch_last_heartbeat(rollback, "st_hb5")

    with rollback.cursor() as cur:
        cur.execute(
            "select last_heartbeat_at from stations where station_id = %s", ("st_hb5",)
        )
        (after,) = cur.fetchone()
    assert after is not None
