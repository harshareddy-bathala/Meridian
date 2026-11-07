"""Datadog watches what Prometheus watches, and is never needed (D-353).

Read from ``deploy/``; nothing here reaches Datadog. The monitors are applied
by ``deploy/tools/datadog_monitors.py`` and seen to fire on the cloud host; this
pins that the file still mirrors every alert rule, queries only metrics the
platform exports, names no recipient, and that the Agent's override reads its
key from a file and changes nothing else about the platform.

Marked as a unit test by living in ``tests/unit``: reads files, runs nothing.
"""

from __future__ import annotations

import importlib.util
import json
import re
from collections import Counter
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
RULES = DEPLOY / "prometheus" / "rules" / "meridian.yml"
MONITORS = DEPLOY / "datadog" / "monitors.json"
OVERRIDE = DEPLOY / "docker-compose.datadog.yml"
TOOL = DEPLOY / "tools" / "datadog_monitors.py"


def _rules() -> list[dict[str, Any]]:
    groups = yaml.safe_load(RULES.read_text())["groups"]
    return [rule for group in groups for rule in group["rules"] if "alert" in rule]


def _monitors() -> list[dict[str, Any]]:
    monitors: list[dict[str, Any]] = json.loads(MONITORS.read_text())["monitors"]
    return monitors


def _tag(monitor: dict[str, Any], key: str) -> str:
    (value,) = [t.split(":", 1)[1] for t in monitor["tags"] if t.startswith(key + ":")]
    return value


def _exported() -> set[str]:
    """Every metric name the platform's source declares, without its prefix."""
    names: set[str] = set()
    for path in (ROOT / "platform" / "src" / "meridian").rglob("*.py"):
        names.update(re.findall(r"\"meridian_([a-z_]+)\"", path.read_text()))
    return names


def test_every_alert_rule_has_a_monitor_at_the_same_severity() -> None:
    rules = {(rule["alert"], rule["labels"]["severity"]) for rule in _rules()}
    monitors = {(_tag(m, "alert"), _tag(m, "severity")) for m in _monitors()}
    assert monitors == rules


def test_a_rule_per_task_is_a_monitor_per_task() -> None:
    """Prometheus has one never-succeeded rule per task; so does Datadog."""
    rules = Counter(rule["alert"] for rule in _rules())
    monitors = Counter(_tag(m, "alert") for m in _monitors())
    for alert, count in rules.items():
        assert monitors[alert] >= count, alert


def test_every_monitor_queries_a_metric_the_platform_exports() -> None:
    exported = _exported()
    for monitor in _monitors():
        for name in re.findall(r"meridian\.([a-z_]+)", monitor["query"]):
            assert name == "openmetrics" or name in exported, (monitor["name"], name)


def test_every_monitor_is_managed_and_names_no_recipient() -> None:
    names = [m["name"] for m in _monitors()]
    assert len(set(names)) == len(names)
    for monitor in _monitors():
        assert "managed:meridian" in monitor["tags"]
        assert "@" not in monitor["message"], "recipients come from DATADOG_NOTIFY"


def _override() -> dict[str, Any]:
    document: dict[str, Any] = yaml.safe_load(OVERRIDE.read_text())
    return document["services"]


def test_the_agent_reads_its_key_from_a_file_it_cannot_write() -> None:
    agent = _override()["datadog-agent"]
    environment = agent["environment"]
    assert environment["DD_API_KEY"] == "ENC[file@/run/secrets/datadog/api_key]"
    assert re.search(r"@sha256:[0-9a-f]{64}$", agent["image"])
    binds = {v["target"]: v for v in agent["volumes"] if isinstance(v, dict)}
    for target in (
        "/run/secrets/datadog/api_key",
        "/run/secrets/meridian/metrics_token",
    ):
        assert binds[target]["read_only"] is True
        assert binds[target]["bind"]["create_host_path"] is False
    host = [v for v in agent["volumes"] if isinstance(v, str)]
    assert host and all(v.endswith(":ro") for v in host)
    assert "ports" not in agent


def test_the_override_only_labels_the_platform_it_scrapes() -> None:
    """Adding the file must not change how the api or the jobs process run."""
    services = _override()
    for name, port in (("api", 8000), ("jobs", 9464)):
        assert set(services[name]) == {"labels"}, name
        checks = json.loads(services[name]["labels"]["com.datadoghq.ad.checks"])
        (instance,) = checks["openmetrics"]["instances"]
        assert instance["openmetrics_endpoint"] == f"http://%%host%%:{port}/metrics"
        assert instance["collect_histogram_buckets"] is False
        # The prefix is stripped before `metrics` is matched (found on the host:
        # "meridian_.*" collected nothing), so the filter is by exclusion.
        assert instance["raw_metric_prefix"] == "meridian_"
        assert instance["metrics"] == [".+"]
        assert instance["exclude_metrics"] == ["^process_", "^python_"]
        reader = instance["auth_token"]["reader"]
        assert reader == {"type": "file", "path": "/run/secrets/meridian/metrics_token"}


def _tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("datadog_monitors", TOOL)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_tool_matches_by_name_and_prunes_only_what_the_file_dropped() -> None:
    tool = _tool()
    want = tool.desired(notify="@team")
    assert all(m["message"].endswith("\n\n@team") for m in want)
    have = {want[0]["name"]: {"id": 7, "name": want[0]["name"]}}
    have["Meridian Gone: retired"] = {"id": 9, "name": "Meridian Gone: retired"}

    create, update, prune = tool.plan(want, have)

    assert [monitor_id for monitor_id, _ in update] == [7]
    assert len(create) == len(want) - 1
    assert [m["id"] for m in prune] == [9]
