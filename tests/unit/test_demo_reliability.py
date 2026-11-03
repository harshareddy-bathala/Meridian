"""The demonstration fleet's one-hour settle margin, and why it is safe.

The settle margin waits for reports a station queued through an outage (D-146,
D-182). ``reliability.demo.toml`` shortens it to an hour for the `demo`
scenario alone, which is only true while that scenario injects nothing that
queues a report. This holds it to that, and holds the file to differing from
the defaults in the margin and nothing else.

No marker: files and constants.

Reference: docs/DECISIONS.md D-146, D-182, D-330, D-333.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from meridian.reliability.config import ReliabilityConfig, load_reliability_config
from meridian_sim.faults import (
    HEARTBEAT_DELAYED,
    NETWORK_DOWN,
    PARTITION,
    RESTART,
    SCENARIOS,
    SLOW_API,
    TOKEN_REVOKED,
    UPLOAD_BLOCKED,
)

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
DEMO = DEPLOY / "reliability.demo.toml"

DELAYS_A_REPORT = frozenset(
    {
        NETWORK_DOWN,
        PARTITION,
        UPLOAD_BLOCKED,
        RESTART,
        HEARTBEAT_DELAYED,
        SLOW_API,
        TOKEN_REVOKED,
    }
)
"""Every fault that leaves a finished observation in a station's queue."""


def test_the_demo_scenario_injects_nothing_that_queues_a_report() -> None:
    assert DELAYS_A_REPORT.isdisjoint(SCENARIOS["demo"])


def test_the_demo_file_changes_the_settle_margin_and_nothing_else() -> None:
    demo = load_reliability_config(DEMO)
    default = ReliabilityConfig()

    assert demo.classification.settle_margin_s == 3600
    assert tomllib.loads(DEMO.read_text()) == {
        "classification": {"settle_margin_s": 3600}
    }
    assert demo.diagnosis == default.diagnosis
    assert demo.classification.sha256() != default.classification.sha256()


def test_the_override_names_the_file_for_the_api_and_the_jobs_service() -> None:
    override = (DEPLOY / "docker-compose.demo.yml").read_text()

    assert override.count("/app/deploy/reliability.demo.toml") == 2
