"""``PsycopgRegistry.was_listening()`` against real TimescaleDB.

The whole chain in one place: the Doppler tolerance the registry applies, the
predicate the store layer runs, and the ``assignments`` join that stops a
station vouching for itself. Kept in its own file rather than folded into
``test_psycopg_registry.py`` because every assertion here is a claim about what
counts as a confirmed miss, and that is the one question the reliability layer
is not allowed to answer for itself.

Each test states one of the five conditions D-056 records. They are written as
separate tests rather than a parametrised table because a failure has to name
which condition broke — "the mode clause stopped filtering" and "the window
clause stopped filtering" are different bugs with the same symptom.

Marked ``integration`` by the directory hook in ``tests/conftest.py``.

Reference: docs/DECISIONS.md D-028, D-056; docs/MSP-SPEC.md §4.2.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from meridian.registry import ListeningQuery  # noqa: E402 — after importorskip
from meridian.registry.doppler_tolerance import doppler_tolerance_hz  # noqa: E402
from meridian.registry.psycopg_registry import PsycopgRegistry  # noqa: E402
from meridian.store.heartbeats import (  # noqa: E402
    ListeningReport,
    NewHeartbeat,
    insert_heartbeat,
)

pytestmark = pytest.mark.integration

STATION_ID = "st_listen"
OTHER_STATION_ID = "st_bystander"
SATELLITE_ID = "norad:57166"
OTHER_SATELLITE_ID = "norad:33591"
ASSIGNMENT_ID = "as_listen"
OTHER_STATIONS_ASSIGNMENT_ID = "as_bystander"

METEOR_LRPT_HZ = 137_900_000
LRPT = "lrpt"
ZERO_HASH = bytes(32)
OTHER_HASH = bytes([1]) * 32

LINE1 = "1 57166U 23091A   26226.50000000  .00001234  00000-0  12345-4 0  9991"
LINE2 = "2 57166  98.6416 247.4627 0006703 130.5360 325.0288 14.22377579123456"


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    """Undo everything this test writes — see test_store_invites.py's twin."""
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def now(rollback: Any) -> datetime:
    """The instant every ``received_at`` in this file carries.

    ``heartbeats.received_at`` defaults to ``now()``, which in PostgreSQL is the
    start of the current transaction — and every test here runs inside one
    rolled-back transaction. So each heartbeat lands on exactly this instant,
    and a window is placed around it by arithmetic rather than by sleeping.
    """
    with rollback.cursor() as cur:
        cur.execute("select now()")
        row = cur.fetchone()
    assert row is not None
    return row[0]


@pytest.fixture(autouse=True)
def scene(rollback: Any) -> None:
    """Two stations, two satellites, and one assignment issued to each station.

    Autouse because every test in this file needs the same row graph and none
    varies it — naming it in eleven signatures would say nothing the file's
    contents do not.

    The bystander exists for one reason: the join in ``has_listening_evidence``
    is on ``station_id`` as well as ``assignment_id``, and without a real
    assignment belonging to somebody else there is no way to tell that clause
    from a plain existence check.
    """
    _insert_stations(rollback)
    _insert_satellites(rollback)
    pass_id = _insert_pass(rollback)
    _insert_assignment(rollback, ASSIGNMENT_ID, STATION_ID, pass_id)
    _insert_assignment(
        rollback, OTHER_STATIONS_ASSIGNMENT_ID, OTHER_STATION_ID, pass_id
    )


def _insert_stations(conn: Any) -> None:
    """The observed station and a bystander that owns an assignment of its own."""
    with conn.cursor() as cur:
        cur.executemany(
            "insert into stations (station_id, name, operator, lat_deg, lon_deg,"
            " alt_m, token_sha256, registration_key_sha256)"
            " values (%s, %s, %s, %s, %s, %s, %s, %s)",
            [
                (
                    STATION_ID,
                    "Listening",
                    "tests",
                    12.97,
                    77.59,
                    920.0,
                    ZERO_HASH,
                    ZERO_HASH,
                ),
                (
                    OTHER_STATION_ID,
                    "Bystander",
                    "tests",
                    51.5,
                    -0.1,
                    24.0,
                    OTHER_HASH,
                    OTHER_HASH,
                ),
            ],
        )


def _insert_satellites(conn: Any) -> None:
    """The target and one other, so "wrong satellite" is a real row not a typo."""
    with conn.cursor() as cur:
        cur.executemany(
            "insert into satellites (satellite_id, name) values (%s, %s)",
            [(SATELLITE_ID, "Meteor-M N2-3"), (OTHER_SATELLITE_ID, "NOAA 19")],
        )


def _insert_pass(conn: Any) -> int:
    """One computed pass, with the element set a pass cannot exist without."""
    with conn.cursor() as cur:
        cur.execute(
            "insert into element_sets (satellite_id, epoch, line1, line2, source)"
            " values (%s, %s, %s, %s, %s) returning id",
            (
                SATELLITE_ID,
                datetime(2026, 8, 14, 2, 11, tzinfo=UTC),
                LINE1,
                LINE2,
                "manual",
            ),
        )
        (element_set_id,) = cur.fetchone()
        cur.execute(
            "insert into passes (satellite_id, station_id, aos, los,"
            " max_elevation_deg, max_elevation_at, aos_azimuth_deg, los_azimuth_deg,"
            " element_set_id, min_elevation_deg)"
            " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id",
            (
                SATELLITE_ID,
                STATION_ID,
                datetime(2026, 8, 14, 9, 41, 20, tzinfo=UTC),
                datetime(2026, 8, 14, 9, 52, 7, tzinfo=UTC),
                61.4,
                datetime(2026, 8, 14, 9, 46, 40, tzinfo=UTC),
                10.0,
                200.0,
                element_set_id,
                10.0,
            ),
        )
        (pass_id,) = cur.fetchone()
    return int(pass_id)


def _insert_assignment(
    conn: Any, assignment_id: str, station_id: str, pass_id: int
) -> None:
    """One scheduled assignment, on the pass the fixture built."""
    with conn.cursor() as cur:
        cur.execute(
            "insert into assignments (assignment_id, pass_id, station_id, start_at,"
            " end_at, centre_freq_hz, mode, timing_uncertainty_s, reason)"
            " values (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                assignment_id,
                pass_id,
                station_id,
                datetime(2026, 8, 14, 9, 41, tzinfo=UTC),
                datetime(2026, 8, 14, 9, 53, tzinfo=UTC),
                METEOR_LRPT_HZ,
                LRPT,
                4.2,
                "test fixture",
            ),
        )


def report_listening(
    conn: Any,
    *,
    assignment_id: str = ASSIGNMENT_ID,
    satellite_id: str = SATELLITE_ID,
    freq_hz: int = METEOR_LRPT_HZ,
    mode: str = LRPT,
) -> None:
    """File one heartbeat from ``STATION_ID`` claiming a listening block.

    Written through ``insert_heartbeat`` rather than raw SQL so these tests
    exercise the same path the MSP route uses — including leaving
    ``received_at`` to the column default, which is the whole point of judging
    on the platform's clock.
    """
    insert_heartbeat(
        conn,
        NewHeartbeat(
            station_id=STATION_ID,
            sent_at=datetime(2026, 8, 14, 9, 45, tzinfo=UTC),
            state="listening",
            held_assignments=(ASSIGNMENT_ID,),
            listening=ListeningReport(
                assignment_id=assignment_id,
                satellite_id=satellite_id,
                centre_freq_hz=freq_hz,
                mode=mode,
            ),
            health_json="{}",
            simulated=False,
            clock_offset_s=0.184,
            clock_uncertainty_s=0.05,
        ),
    )


def ask(conn: Any, window: tuple[datetime, datetime]) -> bool:
    """Ask the registry the standard question over ``window``."""
    registry = PsycopgRegistry(
        conn,
        pepper="was-listening-pepper",
        recovery_window_s=900,
        now_utc=datetime.now(UTC),
    )
    return registry.was_listening(
        ListeningQuery(
            station_id=STATION_ID,
            satellite_id=SATELLITE_ID,
            centre_freq_hz=METEOR_LRPT_HZ,
            mode=LRPT,
            window=window,
        )
    )


def around(instant: datetime) -> tuple[datetime, datetime]:
    """A window comfortably containing ``instant``."""
    return (instant - timedelta(minutes=1), instant + timedelta(minutes=1))


def test_a_confirming_heartbeat_inside_the_window_is_evidence(
    rollback: Any, now: datetime
) -> None:
    """The one case that returns True — everything else here is a refusal."""
    report_listening(rollback)

    assert ask(rollback, around(now)) is True


def test_no_heartbeat_at_all_is_not_evidence(rollback: Any, now: datetime) -> None:
    """Absence is not a miss.

    A station that filed nothing has not confirmed it was listening, so the
    reliability layer must not be able to count its silence as a measurement.
    This is the default answer and the reason the method exists.
    """
    assert ask(rollback, around(now)) is False


def test_a_heartbeat_reporting_the_wrong_mode_is_not_evidence(
    rollback: Any, now: datetime
) -> None:
    """D-028's core claim, asserted rather than left in prose.

    A station on 137.9 MHz running an APT demodulator against an LRPT
    transmission decodes nothing, and it is not the satellite's fault. Without
    ``listening_mode`` this test could not be written, which is exactly why the
    column was added before the method that reads it.
    """
    report_listening(rollback, mode="apt")

    assert ask(rollback, around(now)) is False


def test_a_heartbeat_naming_another_satellite_is_not_evidence(
    rollback: Any, now: datetime
) -> None:
    report_listening(rollback, satellite_id=OTHER_SATELLITE_ID)

    assert ask(rollback, around(now)) is False


def test_a_doppler_shifted_frequency_is_still_the_same_target(
    rollback: Any, now: datetime
) -> None:
    """The case an exact frequency comparison would get wrong.

    A station retunes continuously across a pass, so the frequency it reports is
    the shifted one, never the assignment's nominal figure. Both edges of the
    tolerance are checked here: a station at the extreme of what Doppler can
    explain still counts.
    """
    tolerance_hz = doppler_tolerance_hz(METEOR_LRPT_HZ)
    report_listening(rollback, freq_hz=METEOR_LRPT_HZ + tolerance_hz)

    assert ask(rollback, around(now)) is True


def test_a_frequency_beyond_doppler_is_not_evidence(
    rollback: Any, now: datetime
) -> None:
    """One hertz past what the physics allows is a different tuning.

    Off by more than Doppler can explain, the station was listening to
    something — but not to this. Counting it would credit a station for a pass
    it was tuned away from, and every reliability figure downstream inherits it.
    """
    tolerance_hz = doppler_tolerance_hz(METEOR_LRPT_HZ)
    report_listening(rollback, freq_hz=METEOR_LRPT_HZ + tolerance_hz + 1)

    assert ask(rollback, around(now)) is False


def test_a_heartbeat_outside_the_window_is_not_evidence(
    rollback: Any, now: datetime
) -> None:
    """Listening yesterday says nothing about this pass."""
    report_listening(rollback)
    earlier = (now - timedelta(minutes=10), now - timedelta(minutes=5))

    assert ask(rollback, earlier) is False


def test_the_window_includes_its_start_and_excludes_its_end(
    rollback: Any, now: datetime
) -> None:
    """Half-open, so two adjacent windows cannot both claim one heartbeat.

    Asserted on the exact boundary instant, which is the only place the
    convention is observable — and the place a ``between`` would silently
    double-count.
    """
    report_listening(rollback)

    assert ask(rollback, (now, now + timedelta(minutes=1))) is True
    assert ask(rollback, (now - timedelta(minutes=1), now)) is False


def test_an_invented_assignment_id_is_not_evidence(
    rollback: Any, now: datetime
) -> None:
    """Nothing in the schema stops a station quoting an assignment that never was.

    ``heartbeats.listening_assignment_id`` carries no foreign key — it must not,
    because a heartbeat is accepted before its contents are reconciled. So the
    join in ``has_listening_evidence`` is the only thing standing between a
    station and the ability to certify its own work.
    """
    report_listening(rollback, assignment_id="as_never_issued")

    assert ask(rollback, around(now)) is False


def test_another_stations_assignment_is_not_evidence(
    rollback: Any, now: datetime
) -> None:
    """A real assignment id, quoted by a station it was not issued to.

    The sharper half of the same guard: checking only that the assignment exists
    would pass here. The bystander's assignment is on the same pass, the same
    frequency and the same mode — the station is the only thing that differs.
    """
    report_listening(rollback, assignment_id=OTHER_STATIONS_ASSIGNMENT_ID)

    assert ask(rollback, around(now)) is False
