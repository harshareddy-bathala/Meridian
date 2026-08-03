"""The directory-to-marker rule works, and duplicate basenames do not collide.

Both of these are test-infrastructure properties rather than product behaviour,
and both fail *silently*, which is why they get assertions instead of trust.

The marker rule was originally written as ``pytestmark = pytest.mark.e2e`` in
``tests/e2e/conftest.py``. That does nothing — pytest honours ``pytestmark`` in
test modules only. Nothing errors; ``-m e2e`` simply selects zero tests and the
e2e tests run inside the unit job, where a ten-minute compose bring-up appears in
a job that should take two seconds. This file is the reason that cannot recur.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
UNIT_SELECTOR = "not integration and not e2e and not msp_conformance"


def _collect(*args: str) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    return result.stdout + result.stderr


@pytest.mark.parametrize(
    ("directory", "marker"),
    [
        ("integration", "integration"),
        ("msp_conformance", "msp_conformance"),
        ("e2e", "e2e"),
    ],
)
def test_directory_implies_its_marker(directory: str, marker: str) -> None:
    """Selecting a marker collects that directory, and only that directory.

    Runs pytest as a subprocess rather than inspecting the hook, because what
    matters is the behaviour of the command in CI and in the README, not that a
    function was called.
    """
    selected = _collect("-m", marker)
    for line in selected.splitlines():
        if "::" in line:
            assert f"tests/{directory}" in line.replace("\\", "/"), (
                f"-m {marker} collected something outside tests/{directory}: {line}"
            )


def test_marked_directories_never_leak_into_the_unit_run() -> None:
    """The unit selector must collect nothing that needs a service.

    This is the assertion that would have caught the broken `pytestmark`.
    """
    selected = _collect("-m", UNIT_SELECTOR)
    for directory in ("integration", "msp_conformance", "e2e"):
        assert f"tests/{directory}" not in selected.replace("\\", "/"), (
            f"tests/{directory} leaked into the unit run"
        )


def test_unit_directory_is_selected_by_the_unit_selector() -> None:
    """The other half: excluding the markers must not exclude everything."""
    selected = _collect("-m", UNIT_SELECTOR)
    assert "tests/unit" in selected.replace("\\", "/")


def test_duplicate_basenames_across_directories_are_collectable() -> None:
    """`tests/unit/test_x.py` and `tests/integration/test_x.py` must coexist.

    Under pytest's default "prepend" import mode they do not: collection dies
    with "import file mismatch" and the entire run is interrupted, including the
    tests that had nothing to do with it. With four directories holding tests for
    the same modules, that collision is a matter of time rather than of luck —
    hence `--import-mode=importlib` in pyproject.toml.
    """
    probes = [
        REPO_ROOT / "tests" / "unit" / "test_basename_collision_probe.py",
        REPO_ROOT / "tests" / "integration" / "test_basename_collision_probe.py",
    ]
    body = "def test_probe():\n    pass\n"
    for probe in probes:
        probe.write_text(body, encoding="utf-8")
    try:
        output = _collect("tests/unit", "tests/integration")
        assert "import file mismatch" not in output
        assert "error" not in output.lower() or "0 errors" in output.lower()
    finally:
        for probe in probes:
            probe.unlink(missing_ok=True)
