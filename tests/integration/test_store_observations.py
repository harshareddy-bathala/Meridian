"""``meridian.store.observations`` against real TimescaleDB.

Marked ``integration`` by the directory hook in ``tests/conftest.py``. The
``insert_assignment`` fixture is the same minimal row graph
``test_store_assignments.py`` builds — one station, one satellite, one element
set, one pass — because an observation needs every one of them before it can
reference an assignment.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from meridian.store.assignments import (  # noqa: E402 — after importorskip
    mark_assignment_reported,
)
from meridian.store.observations import (  # noqa: E402 — after importorskip
    DopplerSample,
    NewObservation,
    find_latest_observation,
    insert_observation,
    lock_assignment_for_report,
)

pytestmark = pytest.mark.integration

STATION_ID = "st_obs_fixture"
OTHER_STATION_ID = "st_obs_other"
SATELLITE_ID = "norad:99998"
LINE1 = "1 25544U 98067A   26226.50000000  .00001234  00000-0  12345-4 0  9991"
LINE2 = "2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.50377579123456"

STARTED_AT = datetime(2026, 8, 14, 9, 41, 18, tzinfo=UTC)
ENDED_AT = datetime(2026, 8, 14, 9, 52, 10, tzinfo=UTC)

InsertAssignment = Callable[..., None]


def stub_hash(seed: int) -> bytes:
    """A 32-byte stand-in for a real digest, distinct per ``seed``."""
    return bytes([seed % 256]) * 32


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    """Undo everything this test writes — see test_store_assignments.py's twin."""
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def insert_assignment(rollback: Any) -> InsertAssignment:
    """The row graph an observation needs, then a closure over its ``pass_id``."""
    with rollback.cursor() as cur:
        cur.execute(
            "insert into satellites (satellite_id, name) values (%s, %s)",
            (SATELLITE_ID, "Test satellite"),
        )
        # Distinct hashes per station: `stations.token_sha256` is unique, so two
        # rows sharing a placeholder collide before any test runs.
        for index, station_id in enumerate((STATION_ID, OTHER_STATION_ID)):
            cur.execute(
                "insert into stations (station_id, name, operator, lat_deg, lon_deg,"
                " alt_m, token_sha256, registration_key_sha256)"
                " values (%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    station_id,
                    "Test station",
                    "tests",
                    0.0,
                    0.0,
                    0.0,
                    stub_hash(index),
                    stub_hash(index + 100),
                ),
            )
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
                STARTED_AT,
                ENDED_AT,
                61.4,
                datetime(2026, 8, 14, 9, 46, 40, tzinfo=UTC),
                10.0,
                200.0,
                element_set_id,
                10.0,
            ),
        )
        (pass_id,) = cur.fetchone()

    def _insert(
        *,
        assignment_id: str,
        state: str = "held",
        station_id: str = STATION_ID,
    ) -> None:
        with rollback.cursor() as cur:
            cur.execute(
                "insert into assignments (assignment_id, pass_id, station_id, start_at,"
                " end_at, centre_freq_hz, mode, timing_uncertainty_s, reason, state)"
                " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    assignment_id,
                    pass_id,
                    station_id,
                    STARTED_AT,
                    ENDED_AT,
                    137900000,
                    "lrpt",
                    4.2,
                    "test fixture",
                    state,
                ),
            )

    return _insert


def observation(assignment_id: str, **overrides: Any) -> NewObservation:
    """MSP §4.4's example, as the record the platform would derive from it."""
    fields: dict[str, Any] = {
        "assignment_id": assignment_id,
        "station_id": STATION_ID,
        "satellite_id": SATELLITE_ID,
        "started_at": STARTED_AT,
        "ended_at": ENDED_AT,
        "outcome": "decoded",
        "signal_detected": True,
        "first_detection_at": datetime(2026, 8, 14, 9, 41, 53, tzinfo=UTC),
        "peak_snr_db": 11.4,
        "doppler_samples": (
            DopplerSample(datetime(2026, 8, 14, 9, 41, 53, tzinfo=UTC), 3140),
        ),
        "products": ({"kind": "image", "uri": "file:///x.png", "frames": 412},),
        "client_notes": "rotator lagged 2s at AOS",
        "simulated": False,
    }
    fields.update(overrides)
    return NewObservation(**fields)


def expected_observation_id(assignment_id: str, revision: int) -> str:
    """D-027's formula, recomputed here rather than read back from the row."""
    digest = hashlib.sha256(f"{assignment_id}:{revision}".encode()).hexdigest()
    return f"ob_{digest[:12]}"


def test_a_first_observation_is_revision_one(
    rollback: Any, insert_assignment: InsertAssignment
) -> None:
    """The head of an empty lineage is nothing; after one insert it is revision 1."""
    insert_assignment(assignment_id="as_first")
    assert find_latest_observation(rollback, "as_first") is None

    observation_id = insert_observation(
        rollback, observation("as_first"), revision=1, content_sha256=b"\x01" * 32
    )

    assert observation_id == expected_observation_id("as_first", 1)
    latest = find_latest_observation(rollback, "as_first")
    assert latest is not None
    assert latest.revision == 1
    assert latest.content_sha256 == b"\x01" * 32
    assert latest.observation_id == observation_id


def test_a_correction_appends_and_leaves_the_first_revision_intact(
    rollback: Any, insert_assignment: InsertAssignment
) -> None:
    """D-015: the earlier report survives underneath the correction.

    This is the property that makes the store the system of record. An overwrite
    would destroy the evidence that the station said something different first,
    which is exactly the thing a reliability figure would later be challenged on.
    """
    insert_assignment(assignment_id="as_corrected")
    insert_observation(
        rollback, observation("as_corrected"), revision=1, content_sha256=b"\x01" * 32
    )
    insert_observation(
        rollback,
        observation("as_corrected", outcome="signal_no_decode"),
        revision=2,
        content_sha256=b"\x02" * 32,
    )

    with rollback.cursor() as cur:
        cur.execute(
            "select revision, outcome from observations"
            " where assignment_id = %s order by revision",
            ("as_corrected",),
        )
        assert cur.fetchall() == [(1, "decoded"), (2, "signal_no_decode")]

        cur.execute(
            "select revision from observations_current where assignment_id = %s",
            ("as_corrected",),
        )
        assert cur.fetchone() == (2,)


def test_an_observation_with_no_signal_stores_null_detection(
    rollback: Any, insert_assignment: InsertAssignment
) -> None:
    """`no_signal` is data, not a gap — the row exists and carries no detection."""
    insert_assignment(assignment_id="as_quiet")

    insert_observation(
        rollback,
        observation(
            "as_quiet",
            outcome="no_signal",
            signal_detected=False,
            first_detection_at=None,
            peak_snr_db=None,
            doppler_samples=None,
            products=(),
        ),
        revision=1,
        content_sha256=b"\x03" * 32,
    )

    with rollback.cursor() as cur:
        cur.execute(
            "select outcome, first_detection_at, doppler_samples, products_json"
            " from observations where assignment_id = %s",
            ("as_quiet",),
        )
        outcome, first_detection_at, doppler, products = cur.fetchone()
    assert (outcome, first_detection_at, doppler, products) == (
        "no_signal",
        None,
        None,
        [],
    )


def test_doppler_samples_are_stored_in_the_shape_the_wire_used(
    rollback: Any, insert_assignment: InsertAssignment
) -> None:
    """`t` and `offset_hz`, so the column and MSP §4.4 read the same."""
    insert_assignment(assignment_id="as_doppler")

    insert_observation(
        rollback, observation("as_doppler"), revision=1, content_sha256=b"\x04" * 32
    )

    with rollback.cursor() as cur:
        cur.execute(
            "select doppler_samples from observations where assignment_id = %s",
            ("as_doppler",),
        )
        (samples,) = cur.fetchone()
    assert [set(one) for one in samples] == [{"t", "offset_hz"}]
    assert samples[0]["offset_hz"] == 3140


def test_locking_returns_the_owner_and_the_satellite_behind_the_pass(
    rollback: Any, insert_assignment: InsertAssignment
) -> None:
    """One query answers ownership and derives `satellite_id` (D-071)."""
    insert_assignment(assignment_id="as_locked", state="in_progress")

    found = lock_assignment_for_report(rollback, "as_locked")

    assert found is not None
    assert (found.station_id, found.state, found.satellite_id) == (
        STATION_ID,
        "in_progress",
        SATELLITE_ID,
    )


def test_locking_an_unknown_assignment_finds_nothing(rollback: Any) -> None:
    """The `unknown_assignment` case, which the caller turns into a 404."""
    assert lock_assignment_for_report(rollback, "as_nonexistent") is None


@pytest.mark.parametrize("state", ["issued", "held", "in_progress"])
def test_reporting_moves_every_state_that_is_not_expired(
    rollback: Any, insert_assignment: InsertAssignment, state: str
) -> None:
    """`issued` is included because a station can execute without heartbeating."""
    insert_assignment(assignment_id="as_moving", state=state)

    moved = mark_assignment_reported(
        rollback, station_id=STATION_ID, assignment_id="as_moving"
    )

    assert moved
    with rollback.cursor() as cur:
        cur.execute(
            "select state from assignments where assignment_id = %s", ("as_moving",)
        )
        assert cur.fetchone() == ("reported",)


def test_an_expired_assignment_is_left_alone(
    rollback: Any, insert_assignment: InsertAssignment
) -> None:
    """D-071: a late observation lands, and `expired` stays as the evidence.

    Not a tidying failure. `expired` records that the station stopped naming the
    work, and overwriting it would erase the distinction CLAUDE.md rule 7 rests
    on — leaving a row that claims the station reported on time.
    """
    insert_assignment(assignment_id="as_gone", state="expired")

    moved = mark_assignment_reported(
        rollback, station_id=STATION_ID, assignment_id="as_gone"
    )

    assert not moved
    with rollback.cursor() as cur:
        cur.execute(
            "select state from assignments where assignment_id = %s", ("as_gone",)
        )
        assert cur.fetchone() == ("expired",)


def test_another_station_cannot_report_an_assignment_it_was_not_issued(
    rollback: Any, insert_assignment: InsertAssignment
) -> None:
    """Defence in depth: ingest checks ownership, and so does the update."""
    insert_assignment(assignment_id="as_theirs", station_id=OTHER_STATION_ID)

    moved = mark_assignment_reported(
        rollback, station_id=STATION_ID, assignment_id="as_theirs"
    )

    assert not moved


def test_the_head_of_a_lineage_ignores_other_assignments(
    rollback: Any, insert_assignment: InsertAssignment
) -> None:
    """Two assignments, two lineages — one's revision is not the other's head."""
    insert_assignment(assignment_id="as_one")
    insert_assignment(assignment_id="as_two")
    insert_observation(
        rollback, observation("as_one"), revision=1, content_sha256=b"\x05" * 32
    )
    insert_observation(
        rollback, observation("as_one"), revision=2, content_sha256=b"\x06" * 32
    )
    insert_observation(
        rollback, observation("as_two"), revision=1, content_sha256=b"\x07" * 32
    )

    one = find_latest_observation(rollback, "as_one")
    two = find_latest_observation(rollback, "as_two")

    assert one is not None and one.revision == 2
    assert two is not None and two.revision == 1


def test_submitted_at_comes_from_the_platform_clock(
    rollback: Any, insert_assignment: InsertAssignment
) -> None:
    """MSP §6's submission delay is `submitted_at - ended_at`, so both must exist.

    `ended_at` is the station's, from a pass that ran in the past; `submitted_at`
    is the column default. A queued observation keeps its original timestamps and
    the delay falls out of the difference.
    """
    insert_assignment(assignment_id="as_delayed")

    insert_observation(
        rollback, observation("as_delayed"), revision=1, content_sha256=b"\x08" * 32
    )

    with rollback.cursor() as cur:
        cur.execute(
            "select ended_at, submitted_at from observations where assignment_id = %s",
            ("as_delayed",),
        )
        ended_at, submitted_at = cur.fetchone()
    assert ended_at == ENDED_AT
    assert submitted_at - datetime.now(UTC) < timedelta(seconds=5)


LOCKED_STATION_ID = "st_obs_locking"
LOCKED_ASSIGNMENT_ID = "as_contended"
LOCKED_SATELLITE_ID = "norad:99997"


@pytest.fixture
def committed_assignment(database_url: str) -> Iterator[str]:
    """One assignment visible to *other* connections, removed afterwards.

    The rollback fixture every other test uses cannot serve this one: a lock is
    only observable from a second connection, and a second connection cannot see
    rows an uncommitted transaction wrote. So this commits, and cleans up in
    ``finally`` — the one place in the suite that leaves the database briefly
    dirty, because the alternative is not testing D-071's mechanism at all.
    """
    setup = psycopg.connect(database_url, autocommit=True)
    try:
        with setup.cursor() as cur:
            cur.execute(
                "insert into satellites (satellite_id, name) values (%s, %s)",
                (LOCKED_SATELLITE_ID, "Locking fixture"),
            )
            cur.execute(
                "insert into stations (station_id, name, operator, lat_deg, lon_deg,"
                " alt_m, token_sha256, registration_key_sha256)"
                " values (%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    LOCKED_STATION_ID,
                    "Locking station",
                    "tests",
                    0.0,
                    0.0,
                    0.0,
                    stub_hash(7),
                    stub_hash(107),
                ),
            )
            cur.execute(
                "insert into element_sets (satellite_id, epoch, line1, line2, source)"
                " values (%s, %s, %s, %s, %s) returning id",
                (
                    LOCKED_SATELLITE_ID,
                    datetime(2026, 8, 14, 2, 11, tzinfo=UTC),
                    LINE1,
                    LINE2,
                    "manual",
                ),
            )
            (element_set_id,) = cur.fetchone()
            cur.execute(
                "insert into passes (satellite_id, station_id, aos, los,"
                " max_elevation_deg, max_elevation_at, aos_azimuth_deg,"
                " los_azimuth_deg, element_set_id, min_elevation_deg)"
                " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id",
                (
                    LOCKED_SATELLITE_ID,
                    LOCKED_STATION_ID,
                    STARTED_AT,
                    ENDED_AT,
                    61.4,
                    datetime(2026, 8, 14, 9, 46, 40, tzinfo=UTC),
                    10.0,
                    200.0,
                    element_set_id,
                    10.0,
                ),
            )
            (pass_id,) = cur.fetchone()
            cur.execute(
                "insert into assignments (assignment_id, pass_id, station_id,"
                " start_at, end_at, centre_freq_hz, mode, timing_uncertainty_s,"
                " reason, state) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    LOCKED_ASSIGNMENT_ID,
                    pass_id,
                    LOCKED_STATION_ID,
                    STARTED_AT,
                    ENDED_AT,
                    137900000,
                    "lrpt",
                    4.2,
                    "locking fixture",
                    "held",
                ),
            )
        yield LOCKED_ASSIGNMENT_ID
    finally:
        # Child rows first: every one of these is a foreign key away from the next.
        removals = (
            ("observations", "assignment_id", LOCKED_ASSIGNMENT_ID),
            ("assignments", "station_id", LOCKED_STATION_ID),
            ("passes", "station_id", LOCKED_STATION_ID),
            ("element_sets", "satellite_id", LOCKED_SATELLITE_ID),
            ("stations", "station_id", LOCKED_STATION_ID),
            ("satellites", "satellite_id", LOCKED_SATELLITE_ID),
        )
        with setup.cursor() as cur:
            for table, column, value in removals:
                cur.execute(f"delete from {table} where {column} = %s", (value,))
        setup.close()


def test_a_second_submission_waits_for_the_first_to_finish(
    database_url: str, committed_assignment: str
) -> None:
    """D-071's whole mechanism: two submissions cannot interleave.

    Without the lock, both would read the same head revision and both would
    append revision 1 — two rows the primary key permits, because it carries
    ``started_at`` and the two submissions can differ in it. The second
    connection sets a short ``lock_timeout`` so a working lock fails fast here
    instead of hanging the suite; in production it simply waits.
    """
    holder = psycopg.connect(database_url)
    waiter = psycopg.connect(database_url)
    try:
        with holder.transaction():
            assert lock_assignment_for_report(holder, committed_assignment) is not None

            with waiter.transaction(), waiter.cursor() as cur:
                cur.execute("set local lock_timeout = '250ms'")
                with pytest.raises(psycopg.errors.LockNotAvailable):
                    lock_assignment_for_report(waiter, committed_assignment)
    finally:
        holder.close()
        waiter.close()


def test_the_lock_is_released_when_the_transaction_ends(
    database_url: str, committed_assignment: str
) -> None:
    """The queued submission proceeds once the first commits, rather than failing."""
    holder = psycopg.connect(database_url)
    waiter = psycopg.connect(database_url)
    try:
        with holder.transaction():
            lock_assignment_for_report(holder, committed_assignment)

        with waiter.transaction(), waiter.cursor() as cur:
            cur.execute("set local lock_timeout = '250ms'")
            assert lock_assignment_for_report(waiter, committed_assignment) is not None
    finally:
        holder.close()
        waiter.close()
