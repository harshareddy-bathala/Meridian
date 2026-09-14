"""The monitoring files agree with each other, checked without Prometheus.

``promtool test rules`` proves each alert's behaviour, and runs in CI. What it
cannot notice is an alert with no test at all — a new rule is simply not
evaluated by a test file that never names it. These tests close that gap, and
check the two cross-references that break silently: a dashboard pointing at a
datasource uid nobody provisioned renders "datasource not found" on every panel,
and an alert whose ``runbook`` names another alert's section sends the operator
to the wrong advice.

Marked as a unit test by living in ``tests/unit``: reads files, runs nothing.

Reference: docs/DECISIONS.md D-111, D-112.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
RULES = DEPLOY / "prometheus/rules/meridian.yml"
RULE_TESTS = DEPLOY / "prometheus/tests/meridian.test.yml"
DASHBOARDS = DEPLOY / "grafana/dashboards"
DATASOURCES = DEPLOY / "grafana/provisioning/datasources/prometheus.yml"
OPERATIONS = DEPLOY.parent / "docs/OPERATIONS.md"


def _alerting_rules() -> list[dict[str, object]]:
    document = yaml.safe_load(RULES.read_text(encoding="utf-8"))
    return [rule for group in document["groups"] for rule in group["rules"]]


def _rule_test_cases() -> list[dict[str, object]]:
    document = yaml.safe_load(RULE_TESTS.read_text(encoding="utf-8"))
    return [case for test in document["tests"] for case in test["alert_rule_test"]]


def test_every_alert_has_a_firing_and_a_silent_test() -> None:
    names = {str(rule["alert"]) for rule in _alerting_rules()}
    cases = _rule_test_cases()
    fires = {case["alertname"] for case in cases if case["exp_alerts"]}
    stays_silent = {case["alertname"] for case in cases if not case["exp_alerts"]}

    assert names - fires == set(), "alerts never shown to fire"
    assert names - stays_silent == set(), "alerts never shown to stay silent"


def test_every_alert_links_its_own_runbook_section_and_has_a_severity() -> None:
    for rule in _alerting_rules():
        name = str(rule["alert"])
        annotations = rule["annotations"]
        labels = rule["labels"]
        assert isinstance(annotations, dict)
        assert isinstance(labels, dict)
        assert annotations["runbook"] == f"docs/OPERATIONS.md#{name.lower()}"
        assert annotations["summary"]
        assert labels["severity"] in {"critical", "warning"}


def test_every_runbook_link_lands_on_a_section() -> None:
    """A ``runbook`` annotation naming a missing heading is a link to nothing.

    GitHub derives ``#apiunavailable`` from a ``### ApiUnavailable`` heading, so
    the heading must be the alert's name exactly.
    """
    headings = {
        line.removeprefix("### ").strip()
        for line in OPERATIONS.read_text(encoding="utf-8").splitlines()
        if line.startswith("### ")
    }

    for rule in _alerting_rules():
        assert rule["alert"] in headings, f"no runbook section for {rule['alert']}"


def test_no_alert_is_labelled_with_an_identifier() -> None:
    """Station, satellite and assignment ids never become labels (D-111)."""
    for rule in _alerting_rules():
        expression = str(rule["expr"])
        assert "station_id" not in expression
        assert "norad" not in expression
        assert "assignment_id" not in expression


def test_every_dashboard_reads_the_provisioned_datasource() -> None:
    provisioned = {
        source["uid"]
        for source in yaml.safe_load(DATASOURCES.read_text(encoding="utf-8"))[
            "datasources"
        ]
    }
    dashboards = sorted(DASHBOARDS.glob("*.json"))
    assert dashboards

    for path in dashboards:
        dashboard = json.loads(path.read_text(encoding="utf-8"))
        for panel in dashboard["panels"]:
            for query in panel.get("targets", []):
                assert query["datasource"]["uid"] in provisioned, (
                    f"{path.name}: {panel['title']}"
                )
