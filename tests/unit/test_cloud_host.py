"""The cloud host is built from files, and opens nothing (D-129, Stage 36).

Read from ``deploy/cloud/aws/`` as text and YAML; nothing here calls AWS. The
host itself is checked by ``verify_public_surface.py`` once it runs. This pins
that the files describing it still say what the runbook claims: no inbound
rule, a token-only metadata service, a database volume kept on termination, no
secret in user data, and timers that point where cloud-init puts the checkout.

Marked as a unit test by living in ``tests/unit``: reads files, runs nothing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
AWS = ROOT / "deploy" / "cloud" / "aws"
SYSTEMD = ROOT / "deploy" / "systemd"

PACKAGES = {
    "ca-certificates",
    "git",
    "python3",
    "docker-ce",
    "docker-ce-cli",
    "containerd.io",
    "docker-compose-plugin",
}
"""What the host installs: Docker, and what the checkout and its tools need."""


def _cloud_init() -> dict[str, Any]:
    text = (AWS / "cloud-init.yaml").read_text()
    # cloud-init ignores user data that does not open with its header.
    assert text.startswith("#cloud-config\n")
    loaded = yaml.safe_load(text)
    assert isinstance(loaded, dict)
    return loaded


def _runbook() -> str:
    return (AWS / "README.md").read_text()


def _commands(config: dict[str, Any]) -> list[str]:
    steps = config["runcmd"]
    return [" ".join(step) if isinstance(step, list) else step for step in steps]


def test_the_host_installs_only_what_the_runbook_names() -> None:
    assert set(_cloud_init()["packages"]) == PACKAGES


def test_user_data_carries_no_secret_and_starts_nothing() -> None:
    """User data is readable from the instance's metadata service, so secrets
    are written by hand after sign-in, and the stack is started then too."""
    config = _cloud_init()
    assert "write_files" not in config
    for command in _commands(config):
        assert "compose" not in command
        assert "secret" not in command
        assert "token" not in command


def test_the_timers_point_where_cloud_init_puts_the_checkout() -> None:
    config = _cloud_init()
    clone = next(c for c in _commands(config) if c.startswith("git clone"))
    checkout = clone.split()[-1]
    accounts = {user["name"] for user in config["users"] if isinstance(user, dict)}
    units = sorted(SYSTEMD.glob("meridian-*.service"))
    assert units, "cloud-init copies units that must exist"
    for unit in units:
        text = unit.read_text()
        assert f"WorkingDirectory={checkout}\n" in text, unit.name
        user = re.search(r"^User=(\S+)$", text, re.MULTILINE)
        assert user is not None and user.group(1) in accounts, unit.name


def test_the_account_runs_docker_and_cannot_sign_in() -> None:
    (account,) = [u for u in _cloud_init()["users"] if isinstance(u, dict)]
    assert account["groups"] == ["docker"]
    assert account["lock_passwd"] is True
    assert "ssh_authorized_keys" not in account


def test_no_inbound_rule_is_ever_added() -> None:
    runbook = _runbook()
    assert "create-security-group" in runbook
    assert "authorize-security-group-ingress" not in runbook
    assert "key-name" not in runbook, "the shell is Session Manager, never an SSH key"


def test_the_instance_requires_metadata_tokens_and_keeps_its_volume() -> None:
    runbook = _runbook()
    assert "HttpTokens=required" in runbook
    assert "HttpPutResponseHopLimit=1" in runbook, "containers must not reach the role"
    assert '"Encrypted":true' in runbook
    assert '"DeleteOnTermination":false' in runbook
    assert "--disable-api-termination" in runbook
    assert "--user-data file://deploy/cloud/aws/cloud-init.yaml" in runbook


def test_only_ec2_may_assume_the_role() -> None:
    policy = json.loads((AWS / "ec2-trust.json").read_text())
    (statement,) = policy["Statement"]
    assert statement["Principal"] == {"Service": "ec2.amazonaws.com"}
    assert statement["Action"] == "sts:AssumeRole"
    assert "AmazonSSMManagedInstanceCore" in _runbook()


def test_no_amount_is_written_down() -> None:
    """The budget's limit is set when the alert is created, never recorded here."""
    assert not re.search(r"\$\s?\d|\d+\s?(USD|dollars)", _runbook())
