"""Stage 29's gate: owner reports, delivered, and regenerated to their hashes.

*In a rehearsal of the demonstration, a decoded pass and a diagnosed loss each
produce a delivered report whose text regenerates to its recorded content
hash.*

A small fleet flies the ``diagnosis`` scenario against the real application in
a database of its own, through the harness SC-8's runs are made with
(``deploy/tools/diagnosis_runs.py``), whose pinned seed loses passes to a dead
receiver and decodes others. The platform classifies and diagnoses them as the
jobs service does. Then, as the ``reports`` task and the ``notifications``
service do, every station's reports are queued and sent, to the sink, because
every station of the fleet is simulated. The gate reads what was delivered:

* **a decoded pass and a diagnosed loss each delivered a report**;
* **each delivered file hashes to its recorded content hash**, and
  ``meridian notify regenerate`` says each regenerates identically, through the
  command, from the database alone;
* **a report that no longer matches is caught**: a changed hash is reported as
  differing, the positive control;
* **every report is simulated, says so first, and went to the sink only**;
* **queuing and sending again write and send nothing.**

Reference: docs/SOFTWARE-IMPLEMENTATION-ROADMAP.md Stage 29's completion gate;
docs/DECISIONS.md D-098, D-300 to D-305; EVALUATION.md §11.4.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from meridian.cli import main  # noqa: E402
from meridian.notifications.channels import SinkChannel  # noqa: E402
from meridian.notifications.compose import deployment_of, queue_due  # noqa: E402
from meridian.notifications.config import (  # noqa: E402
    NotificationsConfig,
    Recipient,
)
from meridian.notifications.send import regenerate, send_due  # noqa: E402
from meridian.notifications.templates import SIMULATED_LINE  # noqa: E402
from meridian.registry import psycopg_registry  # noqa: E402
from meridian.reliability.config import ReliabilityConfig  # noqa: E402
from meridian.store.report_deliveries import (  # noqa: E402
    StoredDelivery,
    find_deliveries,
)

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]
CONFIG = ReliabilityConfig()
PLAN = {
    "scenario": "diagnosis",
    "master_seed": 4472,
    "stations": 4,
    "start": datetime(2026, 8, 12, tzinfo=UTC),
    "hours": 12.0,
    "silent_satellite": "norad:57166",
}
"""Stage 27's pinned fleet: a dead receiver loses passes, and others decode."""


@pytest.fixture(scope="module")
def harness() -> Iterator[ModuleType]:
    """The tool SC-8's runs are made with, imported as running it does."""
    sys.path.insert(0, str(REPO / "deploy" / "migrations"))
    spec = importlib.util.spec_from_file_location(
        "diagnosis_runs", REPO / "deploy" / "tools" / "diagnosis_runs.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["diagnosis_runs"] = module
    spec.loader.exec_module(module)
    try:
        yield module
    finally:
        sys.modules.pop("diagnosis_runs", None)


@pytest.fixture(scope="module")
def rehearsed(
    harness: ModuleType, database_url: str, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[tuple[Any, str, Path, list[StoredDelivery]]]:
    """The fleet flown, settled, reported on and delivered to the sink."""
    plan = harness.Plan(**PLAN)
    root = tmp_path_factory.mktemp("rehearsal")
    ids = harness.station_id_for(plan.master_seed)
    original = psycopg_registry.generate_station_id
    psycopg_registry.generate_station_id = lambda: next(ids)
    try:
        with (
            harness.scratch(database_url, plan.master_seed) as scratch_url,
            psycopg.connect(scratch_url) as conn,
        ):
            _, stations = harness.fly(conn, plan, state_dir=root / "work")
            harness.settle(conn, plan, CONFIG)
            now = plan.end + timedelta(
                seconds=CONFIG.classification.settle_margin_s + 2 * 3600
            )
            config = NotificationsConfig(
                stations=tuple(Recipient(one, "sink", weekly=False) for one in stations)
            )
            queue_due(conn, config, deployment_of(CONFIG, None), now)
            send_due(
                conn,
                retry=config.retry,
                channels={"sink": SinkChannel(root / "sink")},
                recipients={},
                now=now,
            )
            conn.commit()
            yield conn, scratch_url, root / "sink", reports_of(conn)
    finally:
        psycopg_registry.generate_station_id = original


def reports_of(conn: Any) -> list[StoredDelivery]:
    return find_deliveries(conn, limit=1_000)


def outcome_of(conn: Any, report: StoredDelivery) -> str | None:
    row = conn.execute(
        "select o.outcome from observations_current o where o.assignment_id = %s",
        (report.assignment_id,),
    ).fetchone()
    return None if row is None else str(row[0])


def decoded_and_lost(
    conn: Any, reports: list[StoredDelivery]
) -> tuple[StoredDelivery, StoredDelivery]:
    decoded = [one for one in reports if outcome_of(conn, one) == "decoded"]
    lost = [
        one
        for one in reports
        if isinstance(one.inputs_json["reception"], dict)
        and one.inputs_json["reception"].get("diagnosis_id") is not None
    ]
    assert decoded, "the rehearsal decoded no pass"
    assert lost, "the rehearsal diagnosed no loss"
    return decoded[0], lost[0]


def test_a_decoded_pass_and_a_diagnosed_loss_each_delivered_a_report(
    rehearsed: tuple[Any, str, Path, list[StoredDelivery]],
) -> None:
    conn, _, _, reports = rehearsed

    decoded, lost = decoded_and_lost(conn, reports)

    assert decoded.state == "sent"
    assert lost.state == "sent"


def test_each_delivered_file_hashes_to_its_recorded_content_hash(
    rehearsed: tuple[Any, str, Path, list[StoredDelivery]],
) -> None:
    _, _, sink, reports = rehearsed

    for report in reports:
        written = (sink / f"{report.id}.txt").read_bytes()
        assert hashlib.sha256(written).digest() == report.content_sha256, report.id


def test_each_regenerates_identically_through_the_command(
    rehearsed: tuple[Any, str, Path, list[StoredDelivery]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    conn, url, _, reports = rehearsed
    decoded, lost = decoded_and_lost(conn, reports)
    monkeypatch.setenv("DATABASE_URL", url)

    for report in (decoded, lost):
        assert main(["notify", "regenerate", str(report.id)]) == 0
        assert f"report {report.id} regenerates identically" in capsys.readouterr().out


def test_a_report_that_no_longer_matches_is_caught(
    rehearsed: tuple[Any, str, Path, list[StoredDelivery]],
) -> None:
    conn, _, _, reports = rehearsed
    _, lost = decoded_and_lost(conn, reports)

    with conn.transaction(force_rollback=True):
        conn.execute(
            "update report_deliveries set content_sha256 = %s where id = %s",
            (bytes(32), lost.id),
        )
        [forged] = [one for one in reports_of(conn) if one.id == lost.id]
        again = regenerate(conn, forged)

    assert again.inputs_match
    assert not again.content_match
    assert regenerate(conn, lost).identical


def test_every_report_is_simulated_says_so_first_and_went_to_the_sink(
    rehearsed: tuple[Any, str, Path, list[StoredDelivery]],
) -> None:
    _, _, sink, reports = rehearsed

    assert reports
    assert all(one.simulated and one.channel == "sink" for one in reports)
    for report in reports:
        body = (sink / f"{report.id}.txt").read_text(encoding="utf-8")
        subject, _, text = body.partition("\n\n")
        assert subject.startswith("[SIMULATED] ")
        assert text.splitlines()[0] == SIMULATED_LINE


def test_queuing_and_sending_again_write_and_send_nothing(
    rehearsed: tuple[Any, str, Path, list[StoredDelivery]],
) -> None:
    conn, _, sink, reports = rehearsed
    plan_end = datetime(2026, 8, 12, 12, tzinfo=UTC)
    later = plan_end + timedelta(days=3)
    config = NotificationsConfig(
        stations=tuple(
            Recipient(one, "sink", weekly=False)
            for one in sorted({one.station_id for one in reports})
        )
    )

    with conn.transaction(force_rollback=True):
        again = queue_due(conn, config, deployment_of(CONFIG, None), later)
        sent = send_due(
            conn,
            retry=config.retry,
            channels={"sink": SinkChannel(sink)},
            recipients={},
            now=later,
        )

    assert again.passes == 0
    assert sent.attempted == 0
