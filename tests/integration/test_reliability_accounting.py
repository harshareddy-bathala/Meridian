"""``meridian.reliability.accounting`` against real TimescaleDB.

Each class a settled pass can be given, reached from the rows that decide it,
with the evidence the stored row keeps. The rules themselves are unit-tested in
``tests/unit/test_reliability_classification.py``; these prove the live path
gathers the right evidence for them, asks the registry rather than reading a
heartbeat itself, and stores what it read.

Reference: docs/DECISIONS.md D-146, D-147, D-165, D-180, D-181, D-182.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from meridian.registry.psycopg_registry import PsycopgRegistry  # noqa: E402
from meridian.reliability.accounting import classify_settled  # noqa: E402
from meridian.reliability.classification import METHOD  # noqa: E402
from meridian.reliability.config import ClassificationConfig  # noqa: E402

pytestmark = pytest.mark.integration

SATELLITE = "norad:99970"
AOS = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
LATER = AOS + timedelta(days=3)
"""Every pass here has settled by this instant under the default margin."""


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    """Undo everything this test writes."""
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def rows(schedule_rows: Any) -> Any:
    """The row builder, with the test satellite and one element set of it."""
    schedule_rows.satellite(SATELLITE)
    return schedule_rows


@pytest.fixture
def element_set(rows: Any) -> int:
    return element_set_of(rows)


def element_set_of(rows: Any) -> int:
    with rows.conn.cursor() as cur:
        cur.execute(
            "select min(id) from element_sets where satellite_id = %s", (SATELLITE,)
        )
        return int(cur.fetchone()[0])


def heartbeat(
    conn: Any, station_id: str, at: datetime, *, listening_to: str | None = None
) -> None:
    """One heartbeat received at ``at``, listening for an assignment if named."""
    listening = (
        (listening_to, SATELLITE, 137_900_000, "lrpt")
        if listening_to is not None
        else (None, None, None, None)
    )
    with conn.cursor() as cur:
        cur.execute(
            "insert into heartbeats (station_id, sent_at, received_at, state,"
            " listening_assignment_id, listening_satellite_id, listening_freq_hz,"
            " listening_mode, simulated)"
            " select %s, %s, %s, %s, %s, %s, %s, %s, simulated"
            " from stations where station_id = %s",
            (
                station_id,
                at,
                at,
                "listening" if listening_to else "idle",
                *listening,
                station_id,
            ),
        )


def run(conn: Any, now: datetime = LATER) -> Any:
    registry = PsycopgRegistry(
        conn, pepper="test-pepper", recovery_window_s=3600, now_utc=now
    )
    return classify_settled(conn, registry, now=now, config=ClassificationConfig())


def stored(conn: Any) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(
            "select assignment_id, assignment_ids, classification, evidence,"
            " method, simulated from pass_classifications order by assignment_id"
        )
        return list(cur.fetchall())


def scheduled_pass(
    rows: Any,
    station: str,
    assignment_id: str,
    *,
    aos: datetime = AOS,
    state: str = "reported",
) -> str:
    """A pass of the test satellite and a scheduled assignment over it."""
    pass_id = rows.pass_(station, aos, element_set_id=element_set_of(rows))
    return rows.assignment(assignment_id, pass_id, state=state)


def listened(conn: Any, station: str, assignment_id: str, aos: datetime = AOS) -> None:
    """The station heard during the window, confirmed listening for this pass."""
    heartbeat(conn, station, aos + timedelta(minutes=3), listening_to=assignment_id)


def only(conn: Any) -> dict[str, Any]:
    held = stored(conn)
    assert len(held) == 1
    return held[0]


def test_a_decode_is_a_successful_reception(rows: Any) -> None:
    rows.station("st_a", simulated=False)
    scheduled_pass(rows, "st_a", "as_1")
    rows.observation("as_1", outcome="decoded")

    report = run(rows.conn)

    row = only(rows.conn)
    assert row["classification"] == "successful_reception"
    assert row["evidence"]["report"]["outcome"] == "decoded"
    assert row["method"] == METHOD
    assert row["simulated"] is False
    assert report.by_class["successful_reception"] == 1


def test_no_report_and_no_heartbeat_is_not_a_miss(rows: Any) -> None:
    """CLAUDE.md rule 7: absence alone is a station that was not there."""
    rows.station("st_a", simulated=False)
    scheduled_pass(rows, "st_a", "as_1", state="held")

    run(rows.conn)

    row = only(rows.conn)
    assert row["classification"] == "station_unavailable"
    assert row["evidence"]["heard_during_window"] is False
    assert row["evidence"]["report"] is None


def test_heard_but_not_listening_is_not_a_miss(rows: Any) -> None:
    rows.station("st_a", simulated=False)
    scheduled_pass(rows, "st_a", "as_1")
    rows.observation("as_1", outcome="no_signal")
    heartbeat(rows.conn, "st_a", AOS + timedelta(minutes=3))

    run(rows.conn)

    row = only(rows.conn)
    assert row["classification"] == "station_not_confirmed_listening"
    assert row["evidence"]["listening_confirmed"] is False


def test_confirmed_silence_while_another_station_heard_it_is_a_miss(rows: Any) -> None:
    rows.station("st_a", simulated=False)
    rows.station("st_b", simulated=False)
    scheduled_pass(rows, "st_a", "as_1")
    rows.observation("as_1", outcome="no_signal")
    listened(rows.conn, "st_a", "as_1")
    scheduled_pass(rows, "st_b", "as_2", aos=AOS + timedelta(hours=2))
    rows.observation("as_2", outcome="decoded")

    run(rows.conn)

    miss = next(one for one in stored(rows.conn) if one["assignment_id"] == "as_1")
    assert miss["classification"] == "confirmed_miss"
    assert miss["evidence"]["listening_confirmed"] is True
    assert miss["evidence"]["satellite"]["state"] == "transmitting"
    assert miss["evidence"]["satellite"]["signal_assignment_ids"] == ["as_2"]


def test_confirmed_silence_everywhere_is_a_silent_satellite(rows: Any) -> None:
    for station, assignment, hours in (("st_a", "as_1", 0), ("st_b", "as_2", 1)):
        rows.station(station, simulated=False)
        aos = AOS + timedelta(hours=hours)
        scheduled_pass(rows, station, assignment, aos=aos)
        rows.observation(assignment, outcome="no_signal")
        listened(rows.conn, station, assignment, aos)
    rows.station("st_c", simulated=False)
    aos = AOS + timedelta(hours=2)
    scheduled_pass(rows, "st_c", "as_3", aos=aos)
    rows.observation("as_3", outcome="no_signal")
    listened(rows.conn, "st_c", "as_3", aos)

    run(rows.conn)

    classes = {one["assignment_id"]: one["classification"] for one in stored(rows.conn)}
    assert classes == dict.fromkeys(("as_1", "as_2", "as_3"), "satellite_silent")


def test_a_silence_not_confirmed_listening_is_no_evidence_of_silence(rows: Any) -> None:
    """The other stations heard nothing, but nothing says they were listening."""
    rows.station("st_a", simulated=False)
    scheduled_pass(rows, "st_a", "as_1")
    rows.observation("as_1", outcome="no_signal")
    listened(rows.conn, "st_a", "as_1")
    for station, assignment, hours in (("st_b", "as_2", 1), ("st_c", "as_3", 2)):
        rows.station(station, simulated=False)
        aos = AOS + timedelta(hours=hours)
        scheduled_pass(rows, station, assignment, aos=aos)
        rows.observation(assignment, outcome="no_signal")

    run(rows.conn)

    row = next(one for one in stored(rows.conn) if one["assignment_id"] == "as_1")
    assert row["classification"] == "satellite_state_indeterminate"
    assert row["evidence"]["satellite"]["silence_assignment_ids"] == []


def test_a_simulated_decode_is_no_evidence_about_a_measured_pass(rows: Any) -> None:
    rows.station("st_a", simulated=False)
    rows.station("st_sim", simulated=True)
    scheduled_pass(rows, "st_a", "as_1")
    rows.observation("as_1", outcome="no_signal")
    listened(rows.conn, "st_a", "as_1")
    scheduled_pass(rows, "st_sim", "as_2", aos=AOS + timedelta(hours=1))
    rows.observation("as_2", outcome="decoded")

    run(rows.conn)

    by_id = {one["assignment_id"]: one for one in stored(rows.conn)}
    assert by_id["as_1"]["classification"] == "satellite_state_indeterminate"
    assert by_id["as_1"]["simulated"] is False
    assert by_id["as_2"]["simulated"] is True


def test_an_expiry_while_heard_is_a_decline_and_unheard_is_unavailable(
    rows: Any,
) -> None:
    """D-181."""
    rows.station("st_a", simulated=False)
    rows.station("st_b", simulated=False)
    scheduled_pass(rows, "st_a", "as_1", state="expired")
    heartbeat(rows.conn, "st_a", AOS + timedelta(minutes=2))
    scheduled_pass(rows, "st_b", "as_2", state="expired")

    run(rows.conn)

    classes = {one["assignment_id"]: one["classification"] for one in stored(rows.conn)}
    assert classes == {"as_1": "assignment_declined", "as_2": "station_unavailable"}


def test_a_window_inside_the_settle_margin_waits(rows: Any) -> None:
    rows.station("st_a", simulated=False)
    scheduled_pass(rows, "st_a", "as_1", state="held")

    report = run(rows.conn, now=AOS + timedelta(hours=12))

    assert (report.classified, stored(rows.conn)) == (0, [])


def test_a_skip_is_never_classified(rows: Any, element_set: int) -> None:
    rows.station("st_a", simulated=False)
    rows.assignment(
        "as_1",
        rows.pass_("st_a", AOS, element_set_id=element_set),
        decision="skipped",
    )

    assert run(rows.conn).classified == 0


def test_two_assignments_of_one_rise_are_one_pass(rows: Any, element_set: int) -> None:
    """Configurations A and B scheduled the same pass: one reception (D-146)."""
    rows.station("st_a", simulated=False)
    pass_id = rows.pass_("st_a", AOS, element_set_id=element_set)
    rows.assignment("as_1", pass_id, model_config="A", state="expired")
    rows.assignment("as_2", pass_id, model_config="B")
    rows.observation("as_2", outcome="decoded")

    run(rows.conn)

    row = only(rows.conn)
    assert row["assignment_ids"] == ["as_1", "as_2"]
    assert row["classification"] == "successful_reception"


def test_running_again_writes_nothing(rows: Any) -> None:
    rows.station("st_a", simulated=False)
    scheduled_pass(rows, "st_a", "as_1")
    rows.observation("as_1", outcome="decoded")

    first = run(rows.conn)
    second = run(rows.conn)

    assert (first.written, second.classified, second.written) == (1, 0, 0)
    assert len(stored(rows.conn)) == 1


def test_a_changed_parameter_classifies_again_beside_the_old_row(rows: Any) -> None:
    rows.station("st_a", simulated=False)
    scheduled_pass(rows, "st_a", "as_1")
    rows.observation("as_1", outcome="decoded")
    run(rows.conn)
    registry = PsycopgRegistry(
        rows.conn, pepper="test-pepper", recovery_window_s=3600, now_utc=LATER
    )

    classify_settled(
        rows.conn,
        registry,
        now=LATER,
        config=ClassificationConfig(silent_window_s=3600),
    )

    assert len(stored(rows.conn)) == 2
