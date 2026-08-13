"""``meridian.observations.ingest`` against real TimescaleDB.

The five invariants ``meridian.observations``' docstring promises — ownership,
provenance, timestamp sanity, idempotency and the assignment transition — land
together in one function, so they are tested together here rather than one file
each. Timestamp sanity is the exception: it needs the platform clock and lives at
the route (D-072), so it is covered in the conformance tests.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from meridian.observations.ingest import (  # noqa: E402 — after importorskip
    NotOwnerError,
    Submission,
    UnknownAssignmentError,
    ingest,
)
from meridian.store.observations import (  # noqa: E402 — after importorskip
    DopplerSample,
    find_latest_observation,
)

pytestmark = pytest.mark.integration

STATION_ID = "st_ingest_real"
SIMULATED_STATION_ID = "st_ingest_sim"
OTHER_STATION_ID = "st_ingest_other"
SATELLITE_ID = "norad:99996"
LINE1 = "1 25544U 98067A   26226.50000000  .00001234  00000-0  12345-4 0  9991"
LINE2 = "2 25544  51.6416 247.4627 0006703 130.5360 325.0288 15.50377579123456"

STARTED_AT = datetime(2026, 8, 14, 9, 41, 18, tzinfo=UTC)
ENDED_AT = datetime(2026, 8, 14, 9, 52, 10, tzinfo=UTC)
DETECTED_AT = datetime(2026, 8, 14, 9, 41, 53, tzinfo=UTC)

InsertAssignment = Callable[..., None]

STATIONS = (
    (STATION_ID, False),
    (SIMULATED_STATION_ID, True),
    (OTHER_STATION_ID, False),
)


def stub_hash(seed: int) -> bytes:
    """A 32-byte stand-in for a real digest, distinct per ``seed``."""
    return bytes([seed % 256]) * 32


def submission(**overrides: Any) -> Submission:
    """MSP §4.4's example, as the facts a station supplies."""
    fields: dict[str, Any] = {
        "assignment_id": "as_ingest",
        "started_at": STARTED_AT,
        "ended_at": ENDED_AT,
        "outcome": "decoded",
        "signal_detected": True,
        "first_detection_at": DETECTED_AT,
        "peak_snr_db": 11.4,
        "doppler_samples": (DopplerSample(DETECTED_AT, 3140),),
        "products": (),
        "client_notes": None,
    }
    fields.update(overrides)
    return Submission(**fields)


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    """Undo everything this test writes — see test_store_observations.py's twin."""
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def insert_assignment(rollback: Any) -> InsertAssignment:
    """Three stations, one satellite, one pass — then a closure to add assignments."""
    with rollback.cursor() as cur:
        cur.execute(
            "insert into satellites (satellite_id, name) values (%s, %s)",
            (SATELLITE_ID, "Ingest fixture"),
        )
        for index, (station_id, simulated) in enumerate(STATIONS):
            cur.execute(
                "insert into stations (station_id, name, operator, lat_deg, lon_deg,"
                " alt_m, token_sha256, registration_key_sha256, simulated,"
                " simulator_run_id, seed) values"
                " (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    station_id,
                    "Ingest station",
                    "tests",
                    0.0,
                    0.0,
                    0.0,
                    stub_hash(index + 10),
                    stub_hash(index + 110),
                    simulated,
                    "sim_run_a" if simulated else None,
                    4471 if simulated else None,
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
        assignment_id: str = "as_ingest",
        state: str = "in_progress",
        station_id: str = STATION_ID,
    ) -> None:
        with rollback.cursor() as cur:
            cur.execute(
                "insert into assignments (assignment_id, pass_id, station_id,"
                " start_at, end_at, centre_freq_hz, mode, timing_uncertainty_s,"
                " reason, state) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    assignment_id,
                    pass_id,
                    station_id,
                    STARTED_AT,
                    ENDED_AT,
                    137900000,
                    "lrpt",
                    4.2,
                    "ingest fixture",
                    state,
                ),
            )

    return _insert


def state_of(rollback: Any, assignment_id: str) -> str:
    """The assignment's current state, read straight from the row."""
    with rollback.cursor() as cur:
        cur.execute(
            "select state from assignments where assignment_id = %s", (assignment_id,)
        )
        (state,) = cur.fetchone()
    return str(state)


def revision_count(rollback: Any, assignment_id: str) -> int:
    """How many revisions this assignment's lineage holds."""
    with rollback.cursor() as cur:
        cur.execute(
            "select count(*) from observations where assignment_id = %s",
            (assignment_id,),
        )
        (count,) = cur.fetchone()
    return int(count)


def test_a_first_submission_writes_and_reports_the_assignment(
    rollback: Any, insert_assignment: InsertAssignment
) -> None:
    """Invariant 5: the assignment reaches `reported` (D-008)."""
    insert_assignment()

    ack = ingest(rollback, submission(), station_id=STATION_ID)

    assert ack.assignment_id == "as_ingest"
    assert ack.observation_id.startswith("ob_")
    assert not ack.superseded
    assert revision_count(rollback, "as_ingest") == 1
    assert state_of(rollback, "as_ingest") == "reported"


def test_an_identical_resubmission_writes_nothing_and_repeats_the_answer(
    rollback: Any, insert_assignment: InsertAssignment
) -> None:
    """Invariant 4, end to end: the queued-retry case costs one read and no write."""
    insert_assignment()

    first = ingest(rollback, submission(), station_id=STATION_ID)
    second = ingest(rollback, submission(), station_id=STATION_ID)

    assert second.observation_id == first.observation_id
    assert not second.superseded
    assert revision_count(rollback, "as_ingest") == 1


def test_a_corrected_submission_appends_and_says_so(
    rollback: Any, insert_assignment: InsertAssignment
) -> None:
    """A re-run decoder changes the outcome; both reports survive."""
    insert_assignment()

    first = ingest(rollback, submission(), station_id=STATION_ID)
    second = ingest(
        rollback,
        submission(outcome="signal_no_decode", peak_snr_db=4.0),
        station_id=STATION_ID,
    )

    assert second.superseded
    assert second.observation_id != first.observation_id
    assert revision_count(rollback, "as_ingest") == 2
    latest = find_latest_observation(rollback, "as_ingest")
    assert latest is not None
    assert latest.revision == 2


def test_the_satellite_is_derived_from_the_pass(
    rollback: Any, insert_assignment: InsertAssignment
) -> None:
    """The station never names a satellite in §4.4, so the platform has to know."""
    insert_assignment()

    ingest(rollback, submission(), station_id=STATION_ID)

    with rollback.cursor() as cur:
        cur.execute(
            "select satellite_id from observations where assignment_id = %s",
            ("as_ingest",),
        )
        assert cur.fetchone() == (SATELLITE_ID,)


def test_provenance_comes_from_the_station_row(
    rollback: Any, insert_assignment: InsertAssignment
) -> None:
    """Invariant 2 (D-048): a simulated station's observation is marked simulated.

    `Submission` has no `simulated` field at all, so this is not a matter of
    ignoring a payload value — there is nowhere for one to arrive. The test
    proves the flag still reaches the column.
    """
    insert_assignment(assignment_id="as_sim", station_id=SIMULATED_STATION_ID)

    ingest(
        rollback,
        submission(assignment_id="as_sim"),
        station_id=SIMULATED_STATION_ID,
    )

    with rollback.cursor() as cur:
        cur.execute(
            "select simulated from observations where assignment_id = %s", ("as_sim",)
        )
        assert cur.fetchone() == (True,)


def test_another_stations_assignment_is_refused(
    rollback: Any, insert_assignment: InsertAssignment
) -> None:
    """Invariant 1 (MSP §3): refused before anything is written."""
    insert_assignment(assignment_id="as_theirs", station_id=OTHER_STATION_ID)

    with pytest.raises(NotOwnerError):
        ingest(rollback, submission(assignment_id="as_theirs"), station_id=STATION_ID)

    assert revision_count(rollback, "as_theirs") == 0


def test_an_unknown_assignment_is_refused(rollback: Any) -> None:
    """The 404 case. Nothing exists to own, so ownership never comes up."""
    with pytest.raises(UnknownAssignmentError):
        ingest(rollback, submission(assignment_id="as_ghost"), station_id=STATION_ID)


def test_an_expired_assignment_keeps_its_state_and_still_stores_the_observation(
    rollback: Any, insert_assignment: InsertAssignment
) -> None:
    """D-071: the completion gate says a completed result cannot be lost.

    A station offline long enough to stop naming its assignment comes back with
    a queued observation for a row the platform has expired. Refusing it would
    destroy the result; transitioning would erase the evidence of the outage.
    Both happen: stored, and still `expired`.
    """
    insert_assignment(assignment_id="as_late", state="expired")

    ack = ingest(rollback, submission(assignment_id="as_late"), station_id=STATION_ID)

    assert ack.observation_id.startswith("ob_")
    assert revision_count(rollback, "as_late") == 1
    assert state_of(rollback, "as_late") == "expired"


def test_an_observation_from_an_issued_assignment_still_reports(
    rollback: Any, insert_assignment: InsertAssignment
) -> None:
    """A station that executed without ever heartbeating in between.

    Its row never reached `held`, and leaving it `issued` would keep the pass
    eligible for delivery after it had already been reported.
    """
    insert_assignment(assignment_id="as_silent", state="issued")

    ingest(rollback, submission(assignment_id="as_silent"), station_id=STATION_ID)

    assert state_of(rollback, "as_silent") == "reported"
