"""Every declared entry point exists and answers.

``platform/pyproject.toml`` declares ``meridian = "meridian.cli:main"`` and
``deploy/docker-compose.yml`` runs ``python -m meridian_sim.station``. Both were
declared before either module existed, so the console script died with an
``ImportError`` and the compose ``sim`` profile crash-looped.

The point of these tests is not the shells' contents — those are placeholders and
will be replaced. It is that **a declared executable resolves**, which is a
property that must survive every later change to either file.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )


def test_every_declared_console_script_imports() -> None:
    """Read the entry points out of pyproject and resolve each one.

    Reading them rather than hard-coding the list means a newly declared script
    is covered the moment it is declared, which is exactly when the mismatch is
    introduced.
    """
    import importlib

    for distribution in ("platform", "client", "simulator"):
        pyproject = REPO_ROOT / distribution / "pyproject.toml"
        scripts = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"].get("scripts", {})
        for name, target in scripts.items():
            module_name, _, attribute = target.partition(":")
            module = importlib.import_module(module_name)
            assert callable(getattr(module, attribute)), f"{name} -> {target} is not callable"


def test_meridian_version_succeeds() -> None:
    """The one subcommand that does real work — the image smoke test uses it."""
    from meridian import __version__

    result = _run("-m", "meridian.cli", "--version")
    assert result.returncode == 0
    assert __version__ in result.stdout


@pytest.mark.parametrize(
    "args",
    [
        ["invite", "create", "--label", "nec"],
        ["invite", "list"],
        ["passes", "generate", "--from", "2026-08-02T00:00:00Z", "--to", "2026-08-03T00:00:00Z"],
        ["serve"],
    ],
)
def test_unimplemented_subcommands_report_instead_of_raising(args: list[str]) -> None:
    """Exit 2 with a message naming the stage, never a traceback.

    A stub that raises is discovered by whoever needed the command, at the moment
    they needed it. A stub that answers is discovered by reading its output.
    """
    result = _run("-m", "meridian.cli", *args)
    assert result.returncode == 2
    assert "not implemented yet" in result.stderr
    assert "Stage" in result.stderr
    assert "Traceback" not in result.stderr


def test_simulator_station_runs_as_a_module() -> None:
    """`python -m meridian_sim.station` is what compose runs under `--profile sim`."""
    result = _run("-m", "meridian_sim.station", "--count", "1", "--seed", "4471")
    assert result.returncode == 2
    assert "not implemented yet" in result.stderr
    assert "Traceback" not in result.stderr


def test_simulator_rejects_a_nonsense_station_count() -> None:
    result = _run("-m", "meridian_sim.station", "--count", "0")
    assert result.returncode == 1


def test_readme_does_not_reference_the_pre_d012_module_path() -> None:
    """D-012 renamed `simulator.station` to `meridian_sim.station`.

    A top-level import package called `simulator` is the same class of mistake as
    one called `platform`, just less likely to detonate — and documentation that
    tells a reader to run the old path wastes their time in exactly the way the
    decision was meant to prevent.
    """
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "simulator.station" not in readme
    assert "meridian_sim.station" in readme


def test_simulator_reads_its_compose_environment() -> None:
    """compose passes MERIDIAN_BASE_URL and SIMULATOR_SEED, and no flags at all.

    A parser that ignored them would run against the wrong host while printing
    the right one — the kind of mismatch that is only noticed after an hour of
    wondering why nothing registered.
    """
    import os

    env = {
        **os.environ,
        "MERIDIAN_BASE_URL": "http://api:8000",
        "SIMULATOR_SEED": "9137",
        "SIMULATOR_STATION_COUNT": "5",
    }
    result = subprocess.run(
        [sys.executable, "-m", "meridian_sim.station"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=env,
        check=False,
    )
    assert "http://api:8000" in result.stderr
    assert "9137" in result.stderr
    assert "5 station(s)" in result.stderr
