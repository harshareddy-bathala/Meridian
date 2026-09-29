"""Stage 20's completion gate, asserted clause by clause.

    Every reliability number can be traced back to assignments, observations,
    and heartbeat evidence.

A fleet of measured and simulated stations is classified by the real
accounting, and then:

1. **Every stored classification's evidence is what the source tables say.**
   Each assignment state, report and outcome, heartbeat and listening answer in
   ``evidence`` is checked against ``assignments``, ``observations_current``,
   ``heartbeats`` and ``Registry.was_listening`` directly, not through the code
   that wrote it.
2. **Every number in the report is a count of those rows.** Each indicator's
   numerator and denominator, and the budget's debits by reason, are recounted
   here with SQL over ``pass_classifications``.
3. **The public API publishes the same numbers** as the report.
4. **Absence is not a miss, and the evidence is what makes the difference.**
   The same fleet without one listening heartbeat has one miss fewer, and that
   pass is ``station_not_confirmed_listening``. That is the gate's positive
   control: an assertion that could not fail would prove nothing.
5. **Measured and simulated never meet.** The simulated miss appears in the
   simulated population only.

Reference: docs/DECISIONS.md D-180 to D-187; CLAUDE.md rules 5 and 7.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

psycopg = pytest.importorskip("psycopg")

from meridian.api import platform_clock  # noqa: E402
from meridian.api.app import create_app  # noqa: E402
from meridian.api.dependencies import get_connection  # noqa: E402
from meridian.registry import ListeningQuery  # noqa: E402
from meridian.registry.psycopg_registry import PsycopgRegistry  # noqa: E402
from meridian.reliability.accounting import classify_settled  # noqa: E402
from meridian.reliability.classification import METHOD  # noqa: E402
from meridian.reliability.config import ReliabilityConfig  # noqa: E402
from meridian.reliability.live import read_live_report  # noqa: E402
from meridian.store.reliability_reads import (  # noqa: E402
    count_classified_between,
    find_classified_between,
)

pytestmark = pytest.mark.integration

SATELLITE = "norad:99970"
AOS = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
NOW = AOS + timedelta(days=3)
CONFIG = ReliabilityConfig()


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    with conn.transaction(force_rollback=True):
        yield conn


def registry(conn: Any) -> PsycopgRegistry:
    return PsycopgRegistry(
        conn, pepper="test-pepper", recovery_window_s=3600, now_utc=NOW
    )


def heartbeat(
    conn: Any, station: str, at: datetime, listening_to: str | None = None
) -> None:
    listening = (
        (listening_to, SATELLITE, 137_900_000, "lrpt")
        if listening_to
        else (None, None, None, None)
    )
    with conn.cursor() as cur:
        cur.execute(
            "insert into heartbeats (station_id, sent_at, received_at, state,"
            " listening_assignment_id, listening_satellite_id, listening_freq_hz,"
            " listening_mode, simulated) select %s, %s, %s, %s, %s, %s, %s, %s,"
            " simulated from stations where station_id = %s",
            (
                station,
                at,
                at,
                "listening" if listening_to else "idle",
                *listening,
                station,
            ),
        )


def fleet(rows: Any, *, miss_was_heard: bool = True) -> None:
    """Every class a measured station can be given, and a simulated miss.

    ``as_miss`` is a measured confirmed-listening silence while ``as_heard_b``
    decoded the satellite an hour later, so it is a confirmed miss, unless
    ``miss_was_heard`` is False, in which case its listening heartbeat is left
    out.
    """
    elements = rows.satellite(SATELLITE)
    for station, simulated in (("st_a", False), ("st_b", False), ("st_sim", True)):
        rows.station(station, simulated=simulated)

    def scheduled(station: str, assignment: str, hours: float, state: str) -> datetime:
        aos = AOS + timedelta(hours=hours)
        pass_id = rows.pass_(station, aos, element_set_id=elements)
        rows.assignment(assignment, pass_id, state=state)
        return aos

    aos = scheduled("st_a", "as_decoded", 0, "reported")
    rows.observation("as_decoded", outcome="decoded")
    heartbeat(rows.conn, "st_a", aos + timedelta(minutes=2), "as_decoded")

    aos = scheduled("st_a", "as_miss", 2, "reported")
    rows.observation("as_miss", outcome="no_signal")
    heartbeat(
        rows.conn,
        "st_a",
        aos + timedelta(minutes=3),
        "as_miss" if miss_was_heard else None,
    )
    aos = scheduled("st_b", "as_heard_b", 3, "reported")
    rows.observation("as_heard_b", outcome="decoded")

    scheduled("st_a", "as_absent", 5, "issued")

    aos = scheduled("st_a", "as_declined", 7, "expired")
    heartbeat(rows.conn, "st_a", aos + timedelta(minutes=1))

    aos = scheduled("st_sim", "as_sim_decoded", 0, "reported")
    rows.observation("as_sim_decoded", outcome="decoded")
    aos = scheduled("st_sim", "as_sim_miss", 2, "reported")
    rows.observation("as_sim_miss", outcome="no_signal")
    heartbeat(rows.conn, "st_sim", aos + timedelta(minutes=3), "as_sim_miss")

    classify_settled(
        rows.conn, registry(rows.conn), now=NOW, config=CONFIG.classification
    )


def stored(conn: Any) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute("select * from pass_classifications order by assignment_id")
        return list(cur.fetchall())


def classes(conn: Any) -> dict[str, str]:
    return {one["assignment_id"]: one["classification"] for one in stored(conn)}


# --- 1. evidence matches the source tables ---------------------------------


def test_every_stored_evidence_is_what_the_tables_say(schedule_rows: Any) -> None:
    fleet(schedule_rows)
    conn = schedule_rows.conn
    rows = stored(conn)

    assert len(rows) == 7
    for row in rows:
        evidence = row["evidence"]
        for held in evidence["assignments"]:
            with conn.cursor() as cur:
                cur.execute(
                    "select a.state, a.start_at, a.end_at, a.centre_freq_hz,"
                    " a.mode, p.satellite_id from assignments a"
                    " join passes p on p.id = a.pass_id where a.assignment_id = %s",
                    (held["assignment_id"],),
                )
                state, start, end, freq, mode, satellite = cur.fetchone()
                cur.execute(
                    "select exists (select 1 from heartbeats where station_id = %s"
                    " and received_at >= %s and received_at < %s)",
                    (row["station_id"], start, end),
                )
                (heard,) = cur.fetchone()
                cur.execute(
                    "select outcome from observations_current where assignment_id = %s",
                    (held["assignment_id"],),
                )
                reported = cur.fetchone()
            assert held["state"] == state
            assert evidence["heard_during_window"] == heard
            assert held["listening_confirmed"] == registry(conn).was_listening(
                ListeningQuery(row["station_id"], satellite, freq, mode, (start, end))
            )
            if reported is None:
                assert evidence["report"] is None
            else:
                assert evidence["report"]["outcome"] == reported[0]


# --- 2. every number is a count of stored rows ------------------------------


def recount(conn: Any, simulated: bool) -> dict[str, int]:
    """The report's counts, straight from ``pass_classifications`` in SQL."""
    with conn.cursor() as cur:
        cur.execute(
            """
            select
              count(*) filter (where classification = 'successful_reception'),
              count(*) filter (where classification not in
                  ('satellite_silent', 'satellite_state_indeterminate')),
              count(*) filter (where classification = 'confirmed_miss'),
              count(*) filter (where (evidence ->> 'listening_confirmed')::boolean
                  and classification not in
                  ('satellite_silent', 'satellite_state_indeterminate')),
              count(*) filter (where evidence -> 'report' <> 'null'::jsonb),
              count(*)
            from pass_classifications where simulated = %s
            """,
            (simulated,),
        )
        captured, eligible, missed, listened, reported, passes = cur.fetchone()
    return {
        "captured": captured,
        "eligible": eligible,
        "missed": missed,
        "listened": listened,
        "reported": reported,
        "passes": passes,
    }


@pytest.mark.parametrize("simulated", [False, True], ids=["measured", "simulated"])
def test_every_number_in_the_report_is_a_count_of_stored_rows(
    schedule_rows: Any, simulated: bool
) -> None:
    fleet(schedule_rows)
    report = read_live_report(schedule_rows.conn, now=NOW, config=CONFIG)
    population = report.simulated if simulated else report.measured
    counted = recount(schedule_rows.conn, simulated)

    assert population.passes == counted["passes"]
    assert (population.capture.numerator, population.capture.denominator) == (
        counted["captured"],
        counted["eligible"],
    )
    assert (
        population.confirmed_miss.numerator,
        population.confirmed_miss.denominator,
    ) == (counted["missed"], counted["listened"])
    assert population.completion.numerator == counted["reported"]
    assert population.budget.spent == counted["eligible"] - counted["captured"]
    references = {one.reference for one in population.budget.debits}
    assert references <= {one["assignment_id"] for one in stored(schedule_rows.conn)}


def test_the_scrape_counts_what_the_report_reads(schedule_rows: Any) -> None:
    """The metrics read counts in SQL the rows the report reads one by one."""
    fleet(schedule_rows)
    under = (METHOD, CONFIG.classification.sha256())
    window = (NOW - timedelta(days=30), NOW)
    rows = find_classified_between(
        schedule_rows.conn, classified_under=under, window=window
    )
    tallied: dict[tuple[str, bool], int] = {}
    for one in rows:
        key = (one.classification, one.simulated)
        tallied[key] = tallied.get(key, 0) + 1

    assert rows
    assert (
        count_classified_between(
            schedule_rows.conn, classified_under=under, window=window
        )
        == tallied
    )


def test_the_fleet_is_what_was_built(schedule_rows: Any) -> None:
    """The recount above is not vacuous: the fleet holds a capture, a miss, an
    absence and a decline, and a decode is a capture whatever the heartbeats
    say, because the report is the evidence (D-181's rule 1)."""
    fleet(schedule_rows)

    assert classes(schedule_rows.conn) == {
        "as_absent": "station_unavailable",
        "as_decoded": "successful_reception",
        "as_declined": "assignment_declined",
        "as_heard_b": "successful_reception",
        "as_miss": "confirmed_miss",
        "as_sim_decoded": "successful_reception",
        "as_sim_miss": "confirmed_miss",
    }


# --- 3. the public API publishes the same numbers ---------------------------


def test_the_public_body_is_the_report(
    schedule_rows: Any, rollback: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    fleet(schedule_rows)
    monkeypatch.setattr(platform_clock, "utc_now", lambda: NOW)
    monkeypatch.delenv("MERIDIAN_RELIABILITY_CONFIG", raising=False)
    app = create_app()
    app.dependency_overrides[get_connection] = lambda: rollback
    report = read_live_report(schedule_rows.conn, now=NOW, config=CONFIG)

    with TestClient(app, raise_server_exceptions=False) as client:
        body = client.get("/api/v1/reliability").json()

    for name, population in (
        ("measured", report.measured),
        ("simulated", report.simulated),
    ):
        published = body[name]
        assert published["capture_rate"]["numerator"] == population.capture.numerator
        assert (
            published["capture_rate"]["denominator"] == population.capture.denominator
        )
        assert published["loss_budget"]["by_reason"] == population.budget.by_reason()


# --- 4. absence is not a miss: the positive control -------------------------


def test_without_its_listening_heartbeat_the_miss_is_not_a_miss(
    schedule_rows: Any,
) -> None:
    fleet(schedule_rows, miss_was_heard=False)

    assert classes(schedule_rows.conn)["as_miss"] == "station_not_confirmed_listening"
    report = read_live_report(schedule_rows.conn, now=NOW, config=CONFIG)
    assert report.measured.confirmed_miss.numerator == 0
    assert report.measured.budget.by_reason()["confirmed_miss"] == 0
    assert report.measured.budget.by_reason()["station_not_confirmed_listening"] == 1


# --- 5. measured and simulated never meet ----------------------------------


def test_the_simulated_miss_is_counted_only_among_simulated_stations(
    schedule_rows: Any,
) -> None:
    fleet(schedule_rows)
    report = read_live_report(schedule_rows.conn, now=NOW, config=CONFIG)

    assert report.simulated.confirmed_miss.numerator == 1
    assert report.measured.confirmed_miss.numerator == 1
    assert {one.station_id for one in report.measured.budget.debits} == {"st_a"}
    assert {one.station_id for one in report.simulated.budget.debits} == {"st_sim"}
