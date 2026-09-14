"""The public-surface verifier's judgements, without a network.

The script is an operator tool run against a deployed hostname (D-088), so its
fetches cannot run in CI. Its verdicts can: each check hands a decoded body to a
pure ``judge_*`` function, and those are what decide PASS or FAIL. A verifier
that passed a leaking response would be worse than none, so the failing cases are
the ones pinned.

Marked as a unit test by living in ``tests/unit``: no network, no filesystem
beyond importing the script.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deploy/tools/verify_public_surface.py"


@pytest.fixture(scope="module")
def verifier() -> ModuleType:
    spec = importlib.util.spec_from_file_location("verify_public_surface", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def station(**fields: object) -> dict[str, object]:
    return {"station_id": "st_1", "simulated": True, "liveness": "online"} | fields


def test_a_labelled_list_without_secrets_passes(verifier: ModuleType) -> None:
    outcome, _ = verifier.judge_list("/x", {"items": [station()]}, labelled=True)

    assert outcome == verifier.PASS


def test_an_item_without_provenance_fails(verifier: ModuleType) -> None:
    item = {"station_id": "st_1"}

    outcome, detail = verifier.judge_list("/x", {"items": [item]}, labelled=True)

    assert outcome == verifier.FAIL
    assert "simulated" in detail


def test_the_catalogue_is_not_asked_for_provenance(verifier: ModuleType) -> None:
    outcome, _ = verifier.judge_list(
        "/api/v1/satellites", {"items": [{"satellite_id": "norad:1"}]}, labelled=False
    )

    assert outcome == verifier.PASS


@pytest.mark.parametrize("key", ["seed", "token_sha256", "health_json", "invite"])
def test_a_forbidden_key_fails_the_list(verifier: ModuleType, key: str) -> None:
    outcome, detail = verifier.judge_list(
        "/x", {"items": [station(**{key: "x"})]}, labelled=True
    )

    assert outcome == verifier.FAIL
    assert key in detail


def test_a_virtual_station_must_be_online_to_count(verifier: ModuleType) -> None:
    offline = {"items": [station(liveness="offline")]}
    physical = {"items": [station(simulated=False)]}

    assert verifier.judge_virtual_station({"items": [station()]})[0] == verifier.PASS
    assert verifier.judge_virtual_station(offline)[0] == verifier.FAIL
    assert verifier.judge_virtual_station(physical)[0] == verifier.FAIL


def test_the_burst_is_not_fired_unless_asked(verifier: ModuleType) -> None:
    outcome, _ = verifier.check_rate_limit_is_applied("https://unused.invalid", 0)

    assert outcome == verifier.SKIP
