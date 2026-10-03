"""What a diagnosis reads beside the loss: the station's own records, as of the loss.

Marked ``integration`` by the directory hook. Each read is checked against rows
written for it, and the ones that keep history are checked **as of** an instant:
a profile built after a pass is not evidence about it.

Reference: docs/DECISIONS.md D-102, D-272 to D-277.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

pytest.importorskip("psycopg")

from meridian.store.diagnosis_reads import (
    DeclaredFloor,
    Site,
    find_clock_traces,
    find_declared_floors,
    find_interference_cell,
    find_noise_baseline,
    find_site,
    find_station_history,
    find_transmitter_active,
)
from meridian.store.noise_measurements import record_observation_floor

pytestmark = pytest.mark.integration

START = datetime(2026, 8, 14, 9, 0, tzinfo=UTC)
END = START + timedelta(minutes=11)
STEP = timedelta(minutes=20)


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def world(rollback: Any, schedule_rows: Any) -> Any:
    station = schedule_rows.station("st_reads", simulated=True)
    element_set = schedule_rows.satellite()
    for day in range(6):
        pass_id = schedule_rows.pass_(
            station, START - timedelta(days=day), element_set_id=element_set
        )
        name = f"as_day{day}"
        schedule_rows.assignment(name, pass_id, state="reported")
        schedule_rows.observation(name, outcome="decoded" if day else "no_signal")
    rollback.execute(
        "update observations set receiver_gain_db = 30.0,"
        ' snr_samples = \'[{"t": "2026-08-14T09:00:00Z", "snr_db": 9.5}]\','
        " noise_floor_dbfs = case assignment_id"
        " when 'as_day0' then -55.0 when 'as_day1' then -60.5"
        " when 'as_day2' then -60.0 when 'as_day3' then -59.5 else -60.0 end"
    )
    for day in range(6):
        record_observation_floor(rollback, assignment_id=f"as_day{day}", revision=1)
    return rollback


def heartbeat(conn: Any, received: datetime, skew_s: float, **listening: Any) -> None:
    columns = {
        "listening_assignment_id": None,
        "listening_satellite_id": None,
        "listening_freq_hz": None,
        "listening_mode": None,
        "clock_offset_s": None,
        "clock_uncertainty_s": None,
    } | listening
    conn.execute(
        "insert into heartbeats (station_id, sent_at, received_at, state,"
        " listening_assignment_id, listening_satellite_id, listening_freq_hz,"
        " listening_mode, clock_offset_s, clock_uncertainty_s, simulated)"
        " values ('st_reads', %s, %s, %s, %s, %s, %s, %s, %s, %s, true)",
        (
            received + timedelta(seconds=skew_s),
            received,
            "listening" if columns["listening_assignment_id"] else "idle",
            columns["listening_assignment_id"],
            columns["listening_satellite_id"],
            columns["listening_freq_hz"],
            columns["listening_mode"],
            columns["clock_offset_s"],
            columns["clock_uncertainty_s"],
        ),
    )


def test_the_site_is_the_one_registered(world: Any) -> None:
    assert find_site(world, "st_reads") == Site(12.97, 77.59, 920.0)
    assert find_site(world, "st_nobody") is None


def test_clock_traces_listening_anywhere_and_skew_near_the_window(
    world: Any,
) -> None:
    listening = {
        "listening_assignment_id": "as_day0",
        "listening_satellite_id": "norad:99970",
        "listening_freq_hz": 137_900_000,
        "listening_mode": "lrpt",
    }
    # A clock twenty minutes ahead: it listens before the window opens.
    for minute in range(0, 11, 5):
        heartbeat(world, START - STEP + timedelta(minutes=minute), 1200.0, **listening)
    heartbeat(
        world,
        START + timedelta(minutes=2),
        1200.0,
        clock_offset_s=-3.0,
        clock_uncertainty_s=0.5,
    )
    heartbeat(world, START - timedelta(hours=3), 0.0)

    traces = find_clock_traces(
        world,
        station_id="st_reads",
        assignment_id="as_day0",
        between=(START - timedelta(minutes=15), END + timedelta(minutes=15)),
        listening_between=(START - timedelta(days=1), END + timedelta(days=1)),
    )

    assert traces.listening_span == (START - STEP, START - STEP + timedelta(minutes=10))
    assert traces.skews_s == (1200.0, 1200.0, 1200.0)
    assert traces.reported_offsets == ((-3.0, 0.5),)


def test_no_listening_heartbeat_is_no_span(world: Any) -> None:
    traces = find_clock_traces(
        world,
        station_id="st_reads",
        assignment_id="as_day0",
        between=(START, END),
        listening_between=(START - timedelta(days=1), END + timedelta(days=1)),
    )

    assert traces.listening_span is None
    assert traces.skews_s == ()


def test_the_baseline_is_the_median_of_the_others_at_that_gain(world: Any) -> None:
    median, count = find_noise_baseline(
        world,
        station_id="st_reads",
        gain_db=30.0,
        between=(START - timedelta(days=14), START),
        excluding="as_day0",
    )

    assert count == 5
    assert median == -60.0


def test_a_resubmitted_reception_is_still_one_reading(
    world: Any, schedule_rows: Any
) -> None:
    """A revision writes a noise row of its own; the baseline counts receptions."""
    for revision in range(2, 7):
        schedule_rows.observation("as_day1", revision=revision)
        world.execute(
            "update observations set noise_floor_dbfs = -40.0, receiver_gain_db = 30.0"
            " where assignment_id = 'as_day1' and revision = %s",
            (revision,),
        )
        record_observation_floor(world, assignment_id="as_day1", revision=revision)

    median, count = find_noise_baseline(
        world,
        station_id="st_reads",
        gain_db=30.0,
        between=(START - timedelta(days=14), START),
        excluding="as_day0",
    )

    # Five receptions still, as_day1 now by its latest revision's floor.
    assert count == 5
    assert median == -60.0


def test_listening_is_looked_for_within_its_span_only(world: Any) -> None:
    listening = {
        "listening_assignment_id": "as_day0",
        "listening_satellite_id": "norad:99970",
        "listening_freq_hz": 137_900_000,
        "listening_mode": "lrpt",
    }
    heartbeat(world, START - timedelta(days=3), 0.0, **listening)

    traces = find_clock_traces(
        world,
        station_id="st_reads",
        assignment_id="as_day0",
        between=(START, END),
        listening_between=(START - timedelta(days=1), END + timedelta(days=1)),
    )

    assert traces.listening_span is None


def test_no_reading_at_that_gain_is_no_baseline(world: Any) -> None:
    assert find_noise_baseline(
        world,
        station_id="st_reads",
        gain_db=40.0,
        between=(START - timedelta(days=14), START + timedelta(days=1)),
        excluding="as_day0",
    ) == (None, 0)


def test_history_is_what_the_station_heard_before_the_loss(world: Any) -> None:
    history = find_station_history(
        world, station_id="st_reads", between=(START - timedelta(days=3), START)
    )

    assert [one.assignment_id for one in history] == ["as_day3", "as_day2", "as_day1"]
    assert history[0].snr_samples[0]["snr_db"] == 9.5


def test_the_declared_horizon_is_the_one_in_force_then(world: Any) -> None:
    capability = world.execute(
        "insert into station_capabilities (station_id, band, freq_min_hz,"
        " freq_max_hz, polarisation, min_elevation_deg) values ('st_reads', 'vhf',"
        " 137000000, 138000000, 'rhcp', 0) returning id"
    ).fetchone()[0]
    for built, floor in ((START - timedelta(days=2), 20.0), (START + STEP, 40.0)):
        world.execute(
            "insert into horizon_profiles (station_id, source, method, capability_id,"
            " azimuth_deg, azimuth_width_deg, min_elevation_deg, built_at, simulated)"
            " values ('st_reads', 'declared', 'declared', %s, 90, 40, %s, %s, true)",
            (capability, floor, built),
        )

    assert find_declared_floors(world, station_id="st_reads", at=START) == [
        DeclaredFloor(90.0, 40.0, 20.0)
    ]
    assert (
        find_declared_floors(world, station_id="st_reads", at=START - STEP * 300) == []
    )


def test_the_interference_cell_is_from_a_profile_trained_before(world: Any) -> None:
    for until, lift in ((START - timedelta(days=1), 1.5), (START + STEP, 9.0)):
        world.execute(
            "insert into interference_profiles (station_id, method, dataset_sha256,"
            " trained_from, trained_until, azimuth_deg, azimuth_width_deg,"
            " hour_start, hour_width, noise_lift_db, sample_count, gain_min_db,"
            " gain_max_db, simulated) values ('st_reads', 'd159-v1', %s, %s, %s,"
            " 315, 45, 20, 4, %s, 12, 30, 30, true)",
            (bytes([int(lift)]) * 32, until - timedelta(days=7), until, lift),
        )

    cell = find_interference_cell(
        world, station_id="st_reads", at=START, azimuth_deg=10.0, hour=22
    )

    assert cell is None
    cell = find_interference_cell(
        world, station_id="st_reads", at=START, azimuth_deg=340.0, hour=23
    )
    assert cell is not None
    assert cell.noise_lift_db == 1.5
    assert (
        find_interference_cell(
            world, station_id="st_reads", at=START, azimuth_deg=340.0, hour=1
        )
        is None
    )


def test_the_transmitter_is_read_as_the_catalogue_holds_it(world: Any) -> None:
    world.execute(
        "insert into satellite_transmitters (satellite_id, centre_freq_hz, mode)"
        " values ('norad:99970', 137900000, 'lrpt')"
    )

    assert find_transmitter_active(
        world, satellite_id="norad:99970", centre_freq_hz=137_900_000, mode="lrpt"
    )
    world.execute("update satellite_transmitters set active = false")
    assert (
        find_transmitter_active(
            world, satellite_id="norad:99970", centre_freq_hz=137_900_000, mode="lrpt"
        )
        is False
    )
    assert (
        find_transmitter_active(
            world, satellite_id="norad:99970", centre_freq_hz=1, mode="lrpt"
        )
        is None
    )
