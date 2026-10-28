"""Queuing owner reports — which passes and weeks are due, and what they read.

Marked ``integration`` by the directory hook. What only a database shows:
- a decoded pass is queued once its verdict is in, and only once;
- a lost pass waits for its diagnosis, and says its cause;
- a pass whose verdict or diagnosis never comes is reported after the wait,
  with what there is;
- a report reads exactly the rows it names: a verdict written after it was
  queued does not change what it regenerates to (D-302);
- an expired assignment, or a station the file does not list, gets nothing;
- a simulated station may be listed for the sink only, and an unregistered
  station not at all;
- a week is queued once, from the passes held in it.

Reference: docs/DECISIONS.md D-008, D-300, D-301, D-302, D-306.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from meridian.notifications.compose import Deployment, queue_due  # noqa: E402
from meridian.notifications.config import (  # noqa: E402
    NotificationsConfig,
    NotificationsConfigError,
    Recipient,
    check_stations,
)
from meridian.notifications.facts import inputs_sha256  # noqa: E402
from meridian.notifications.reading import facts_for  # noqa: E402
from meridian.notifications.templates import content_sha256, render  # noqa: E402
from meridian.store.report_deliveries import (  # noqa: E402
    StoredDelivery,
    find_deliveries,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 14, 11, 30, tzinfo=UTC)
"""Two hours after the report world's passes rise."""

DEPLOYMENT = Deployment(
    classification_method="classification-1",
    classification_sha256=bytes([5]) * 32,
    diagnosis_method="diagnosis-1",
    diagnosis_sha256=bytes([6]) * 32,
    verdict_method="verdict-1:abcdefabcdef",
)
"""What the report world in ``conftest.py`` classified, diagnosed and scored by."""
WAIT_S = 6 * 3_600


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    with conn.transaction(force_rollback=True):
        yield conn


def listing(*stations: tuple[str, str]) -> NotificationsConfig:
    return NotificationsConfig(
        stations=tuple(
            Recipient(one, channel, weekly=False) for one, channel in stations
        ),
        wait_s=WAIT_S,
    )


def reports(world: Any, station_id: str) -> list[StoredDelivery]:
    return find_deliveries(world.conn, station_id=station_id)


def regenerates(world: Any, report: StoredDelivery) -> bool:
    facts = facts_for(
        world.conn,
        kind=report.kind,
        station_id=report.station_id,
        period_start=report.period_start,
        inputs=report.inputs_json,
    )
    rendered = render(report.template_version, facts)
    return (
        inputs_sha256(facts) == report.inputs_sha256
        and content_sha256(rendered) == report.content_sha256
    )


def test_a_decoded_pass_is_queued_once_its_verdict_is_in(world: Any) -> None:
    station = world.station("st-decoded")
    subject = world.assignment(station, "a-decoded", state="reported")
    world.rows.observation(subject)
    world.classified(subject, "successful_reception")
    config = listing((station, "email"))

    assert queue_due(world.conn, config, DEPLOYMENT, NOW).passes == 0
    world.verdict(subject, 0.82)
    assert queue_due(world.conn, config, DEPLOYMENT, NOW).passes == 1
    assert queue_due(world.conn, config, DEPLOYMENT, NOW).passes == 0

    [report] = reports(world, station)
    assert (report.kind, report.assignment_id, report.channel) == (
        "pass",
        subject,
        "email",
    )
    assert report.template_version == "pass-1"
    assert regenerates(world, report)


def test_a_partial_decode_waits_for_its_diagnosis(world: Any) -> None:
    station = world.station("st-partial")
    subject = world.assignment(station, "a-partial", state="reported")
    world.rows.observation(subject)
    classification = world.classified(subject, "successful_reception")
    world.verdict(subject, 0.2)
    config = listing((station, "sink"))

    assert queue_due(world.conn, config, DEPLOYMENT, NOW).passes == 0
    world.diagnosis(subject, classification, "interference")
    assert queue_due(world.conn, config, DEPLOYMENT, NOW).passes == 1


def test_a_lost_pass_is_queued_with_its_diagnosed_cause(world: Any) -> None:
    station = world.station("st-lost")
    subject = world.assignment(station, "a-lost", state="reported")
    world.rows.observation(subject, outcome="signal_no_decode")
    classification = world.classified(subject, "signal_no_decode")
    config = listing((station, "sink"))

    assert queue_due(world.conn, config, DEPLOYMENT, NOW).passes == 0
    world.diagnosis(subject, classification, "undetermined")
    assert queue_due(world.conn, config, DEPLOYMENT, NOW).passes == 1

    [report] = reports(world, station)
    facts = facts_for(
        world.conn,
        kind="pass",
        station_id=station,
        period_start=None,
        inputs=report.inputs_json,
    )
    body = render("pass-1", facts).body  # type: ignore[arg-type]
    assert "The cause could not be determined from the evidence." in body
    assert regenerates(world, report)


def test_a_pass_with_nothing_more_coming_is_reported_after_the_wait(
    world: Any,
) -> None:
    station = world.station("st-silent")
    subject = world.assignment(station, "a-silent")
    world.classified(subject, "confirmed_miss")
    config = listing((station, "sink"))

    assert queue_due(world.conn, config, DEPLOYMENT, NOW).passes == 0
    later = NOW + timedelta(seconds=WAIT_S)
    assert queue_due(world.conn, config, DEPLOYMENT, later).passes == 1

    [report] = reports(world, station)
    assert report.inputs_json["reception"] == {
        "assignment_id": subject,
        "revision": None,
        "verdict_method": None,
        "diagnosis_id": None,
    }
    assert regenerates(world, report)


def test_a_report_reads_only_the_rows_it_named(world: Any) -> None:
    station = world.station("st-named")
    subject = world.assignment(station, "a-named", state="reported")
    world.rows.observation(subject)
    world.classified(subject, "successful_reception")
    no_model = replace(DEPLOYMENT, verdict_method=None)
    config = listing((station, "sink"))
    assert queue_due(world.conn, config, no_model, NOW).passes == 1

    world.verdict(subject, 0.9)
    world.rows.observation(subject, revision=2, outcome="no_signal")

    [report] = reports(world, station)
    assert report.inputs_json["reception"]["revision"] == 1  # type: ignore[index]
    assert regenerates(world, report)


def test_a_named_row_that_is_gone_fails_regeneration_loudly(world: Any) -> None:
    station = world.station("st-gone")
    subject = world.assignment(station, "a-gone", state="reported")
    world.rows.observation(subject)
    world.classified(subject, "successful_reception")
    world.verdict(subject, 0.9)
    assert queue_due(world.conn, listing((station, "sink")), DEPLOYMENT, NOW).passes

    [report] = reports(world, station)
    forged = {"reception": {**report.inputs_json["reception"], "revision": 9}}  # type: ignore[dict-item]

    with pytest.raises(LookupError, match="not stored"):
        facts_for(
            world.conn,
            kind="pass",
            station_id=station,
            period_start=None,
            inputs=forged,
        )


def test_an_expired_assignment_or_an_unlisted_station_gets_nothing(
    world: Any,
) -> None:
    listed = world.station("st-listed")
    unlisted = world.station("st-unlisted")
    declined = world.assignment(listed, "a-declined", state="expired")
    world.classified(declined, "assignment_declined")
    elsewhere = world.assignment(unlisted, "a-elsewhere", state="reported")
    world.rows.observation(elsewhere)
    world.classified(elsewhere, "successful_reception")
    world.verdict(elsewhere, 0.9)

    later = NOW + timedelta(days=2)
    assert (
        queue_due(world.conn, listing((listed, "sink")), DEPLOYMENT, later).passes == 0
    )


@pytest.mark.parametrize("channel", ["email", "telegram"])
def test_a_simulated_station_is_refused_on_any_channel_but_the_sink(
    world: Any, channel: str
) -> None:
    station = world.station(f"st-sim-{channel}", simulated=True)

    with pytest.raises(NotificationsConfigError, match="sink only"):
        check_stations(world.conn, listing((station, channel)))
    check_stations(world.conn, listing((station, "sink")))


def test_an_unregistered_station_is_refused(world: Any) -> None:
    with pytest.raises(NotificationsConfigError, match="registered"):
        queue_due(world.conn, listing(("st-nobody", "sink")), DEPLOYMENT, NOW)


def test_a_week_is_queued_once_from_the_passes_held_in_it(world: Any) -> None:
    station = world.station("st-week")
    monday = datetime(2026, 10, 12, tzinfo=UTC)
    decoded = world.assignment(station, "a-week-1", aos=monday + timedelta(days=1))
    world.rows.observation(decoded)
    world.classified(decoded, "successful_reception")
    world.verdict(decoded, 0.8)
    missed = world.assignment(station, "a-week-2", aos=monday + timedelta(days=2))
    world.classified(missed, "confirmed_miss")
    outside = world.assignment(station, "a-week-3", aos=monday + timedelta(days=8))
    world.classified(outside, "confirmed_miss")
    config = NotificationsConfig(stations=(Recipient(station, "sink"),), wait_s=WAIT_S)
    wednesday = datetime(2026, 10, 21, 9, tzinfo=UTC)

    first = queue_due(world.conn, config, DEPLOYMENT, wednesday)
    again = queue_due(world.conn, config, DEPLOYMENT, wednesday)

    assert (first.weeks, again.weeks) == (1, 0)
    [week] = [one for one in reports(world, station) if one.kind == "weekly"]
    assert week.period_start == monday
    facts = facts_for(
        world.conn,
        kind="weekly",
        station_id=station,
        period_start=week.period_start,
        inputs=week.inputs_json,
    )
    assert facts.passes == 2  # type: ignore[union-attr]
    assert facts.outcomes == (("decoded", 1), ("no_report", 1))  # type: ignore[union-attr]
    assert facts.verdicts == (("very likely usable", 1),)  # type: ignore[union-attr]
    assert regenerates(world, week)
