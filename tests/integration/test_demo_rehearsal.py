"""Stage 35's gate, the platform's half: the demonstration, through the public API.

*A rehearsal against the demo profile steps through PROJECT.md §13's software
steps: the fleet is visible, a pass is explained, a loss is diagnosed, a report
is delivered and regenerates to its hash, and every simulated figure carries
SIM.*

The ``demo`` scenario's fleet flies against the real application in a database
of its own, through the harness SC-8's runs are made with
(``deploy/tools/diagnosis_runs.py``). It settles under the demonstration's own
reliability file (D-333), as the jobs service there would. Reports are queued and
sent to the sink, as the ``reports`` task and the ``notifications`` service do.
Then every step is asked of ``/api/v1``, the surface the dashboard reads:

* **§13.1, the fleet is visible:** every station is listed, and every one is
  simulated;
* **§13.2–3, a pass is explained:** a station's schedule holds decisions with
  their explanation and the epoch of the element set behind each, and the
  latest round names its configuration and states its gain as not measured;
* **§13.8, the figures move:** the reliability report counts the fleet's passes
  as simulated and none as measured;
* **§13.9, a loss is diagnosed and reported:** every loss is diagnosed, each
  listed with every cause tested, and a delivered report regenerates to its
  hash;
* **every result is simulated**, in every body the views read.

The browser's half (the views render, nothing breaks the content-security
policy, and with the api stopped each view is served marked as not live) is
``deploy/tools/demo_rehearsal.py``, run against a live stack.

Reference: docs/SOFTWARE-IMPLEMENTATION-ROADMAP.md Stage 35's completion gate;
docs/PROJECT.md §13; docs/DECISIONS.md D-330 to D-335.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from meridian.notifications.channels import SinkChannel  # noqa: E402
from meridian.notifications.compose import deployment_of, queue_due  # noqa: E402
from meridian.notifications.config import (  # noqa: E402
    NotificationsConfig,
    Recipient,
)
from meridian.notifications.send import regenerate, send_due  # noqa: E402
from meridian.registry import psycopg_registry  # noqa: E402
from meridian.reliability import classification  # noqa: E402
from meridian.reliability.config import (  # noqa: E402
    RELIABILITY_CONFIG_ENV,
    load_reliability_config,
)
from meridian.reliability.diagnosis import METHOD  # noqa: E402
from meridian.store.loss_diagnoses import find_undiagnosed  # noqa: E402
from meridian.store.report_deliveries import find_deliveries  # noqa: E402

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]
DEMO_CONFIG = REPO / "deploy" / "reliability.demo.toml"
CONFIG = load_reliability_config(DEMO_CONFIG)
PLAN = {
    "scenario": "demo",
    "master_seed": 4471,
    "stations": 4,
    "start": datetime(2026, 8, 12, tzinfo=UTC),
    "hours": 12.0,
    "silent_satellite": "norad:57166",
}
"""The demonstration's own seed, the first four of its fifty stations: the
fourth steps its clock, the rest carry nothing of their own, and the silence is
the fleet's (D-330). Four, as Stage 27's gate flies, so every build can afford it."""

CAUSES_TESTED = {
    "satellite_silent",
    "station_not_listening",
    "obstruction",
    "interference",
    "timing_fault",
}


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
) -> Iterator[dict[str, Any]]:
    """The fleet flown, settled, reported on, and read through the public API."""
    plan = harness.Plan(**PLAN)
    root = tmp_path_factory.mktemp("demo-rehearsal")
    ids = harness.station_id_for(plan.master_seed)
    original = psycopg_registry.generate_station_id
    psycopg_registry.generate_station_id = lambda: next(ids)
    held = os.environ.get(RELIABILITY_CONFIG_ENV)
    os.environ[RELIABILITY_CONFIG_ENV] = str(DEMO_CONFIG)
    try:
        with (
            harness.scratch(database_url, plan.master_seed) as scratch_url,
            psycopg.connect(scratch_url) as conn,
        ):
            _, stations = harness.fly(conn, plan, state_dir=root / "work")
            harness.settle(conn, plan, CONFIG)
            later = plan.end + timedelta(
                seconds=CONFIG.classification.settle_margin_s + 2 * 3600
            )
            reports = NotificationsConfig(
                stations=tuple(Recipient(one, "sink", weekly=False) for one in stations)
            )
            queue_due(conn, reports, deployment_of(CONFIG, None), later)
            send_due(
                conn,
                retry=reports.retry,
                channels={"sink": SinkChannel(root / "sink")},
                recipients={},
                now=later,
            )
            conn.commit()
            yield {
                "conn": conn,
                "plan": plan,
                "stations": stations,
                "bodies": read_views(harness, conn, plan, stations),
            }
    finally:
        psycopg_registry.generate_station_id = original
        if held is None:
            os.environ.pop(RELIABILITY_CONFIG_ENV, None)
        else:
            os.environ[RELIABILITY_CONFIG_ENV] = held


def read_views(
    harness: ModuleType, conn: Any, plan: Any, stations: tuple[str, ...]
) -> dict[str, Any]:
    """Every body the dashboard's views read, as a visitor would have found them."""
    clock = [plan.start + timedelta(hours=1)]
    with harness.in_process(conn, clock) as client:

        def get(path: str, **params: Any) -> Any:
            response = client.get(path, params=params)
            assert response.status_code == 200, (path, response.text)
            return response.json()

        schedules = {
            one: get("/api/v1/schedule", station_id=one, hours=6) for one in stations
        }
        clock[0] = plan.end + timedelta(
            seconds=CONFIG.classification.settle_margin_s + 3 * 3600
        )
        return {
            "stations": get("/api/v1/stations", limit=200),
            "schedules": schedules,
            "run": get("/api/v1/schedule-runs/latest"),
            "reliability": get("/api/v1/reliability"),
            "losses": get("/api/v1/losses", limit=200),
            "warnings": get("/api/v1/receive-chain-warnings", limit=200),
        }


def test_the_fleet_is_visible_and_every_station_is_simulated(
    rehearsed: dict[str, Any],
) -> None:
    listed = rehearsed["bodies"]["stations"]["items"]

    assert {one["station_id"] for one in listed} == set(rehearsed["stations"])
    assert all(one["simulated"] for one in listed)


def test_a_pass_is_explained_with_the_orbit_behind_it(
    rehearsed: dict[str, Any],
) -> None:
    entries = [
        entry
        for schedule in rehearsed["bodies"]["schedules"].values()
        for entry in schedule["entries"]
    ]
    run = rehearsed["bodies"]["run"]

    assert entries, "no station had a pass in its first six hours"
    explained = [one for one in entries if one["assignment"]["explanation"]]
    assert explained
    assert all(one["element_set_epoch"] for one in explained)
    assert {one["assignment"]["decision"] for one in entries} <= {
        "scheduled",
        "skipped",
    }
    assert run["configuration"] == "A"
    assert run["measured_decisions"] == 0
    assert run["simulated_decisions"] > 0
    assert run["baseline_gain"]["status"] == "not_measured"


def test_the_figures_count_the_fleet_as_simulated(rehearsed: dict[str, Any]) -> None:
    reliability = rehearsed["bodies"]["reliability"]

    assert reliability["simulated"]["passes"] > 0
    assert reliability["measured"]["passes"] == 0
    assert reliability["simulated"]["simulated"] is True


def test_every_loss_is_diagnosed_with_every_cause_tested(
    rehearsed: dict[str, Any],
) -> None:
    conn = rehearsed["conn"]
    losses = rehearsed["bodies"]["losses"]["items"]

    left = find_undiagnosed(
        conn,
        method=METHOD,
        config_sha256=CONFIG.diagnosis.sha256(),
        classification_method=classification.METHOD,
        classification_sha256=CONFIG.classification.sha256(),
        verdict_method=None,
    )
    assert left == []
    assert losses, "the rehearsal's fleet lost no pass"
    for loss in losses:
        assert {one["cause"] for one in loss["tested"]} == CAUSES_TESTED
        assert loss["window_start"].endswith(":00Z")


def test_a_loss_is_reported_and_its_report_regenerates(
    rehearsed: dict[str, Any],
) -> None:
    conn = rehearsed["conn"]
    delivered = [
        one for one in find_deliveries(conn, limit=1_000) if one.state == "sent"
    ]

    assert delivered, "no report reached the sink"
    assert all(one.simulated for one in delivered)
    assert all(regenerate(conn, one).identical for one in delivered)


def test_every_result_a_view_reads_is_simulated(rehearsed: dict[str, Any]) -> None:
    bodies = rehearsed["bodies"]
    flagged = [
        *(one["simulated"] for one in bodies["losses"]["items"]),
        *(one["simulated"] for one in bodies["warnings"]["items"]),
        *(one["simulated"] for one in bodies["schedules"].values()),
        *(
            entry["assignment"]["simulated"]
            for schedule in bodies["schedules"].values()
            for entry in schedule["entries"]
        ),
    ]

    assert flagged
    assert all(flagged)
