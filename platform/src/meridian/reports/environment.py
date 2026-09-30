"""What made a run: the code, the interpreter and the libraries, recorded unhashed.

The roadmap asks every run to record its code commit and dependency versions.
They are recorded in the manifest's ``environment`` block and **left out of
the hash** (D-235): the hash names the result, and two machines that produce
the same numbers from the same snapshot, configuration and seed have produced
the same result. When they do not, ``meridian report verify`` prints what
differs here beside the two hashes, which is the first place to look.

**The commit comes from git when the code is a checkout**, with whether the
tree had uncommitted changes, since a commit alone does not name code that was
edited after it. An installed copy has no ``.git``, so ``MERIDIAN_COMMIT`` is
read instead, and a copy with neither says ``unknown`` rather than guessing.

This module runs ``git`` and reads installed package metadata. It is the only
part of a report that looks at the machine, and nothing it returns reaches a
hashed file.

Reference: docs/DECISIONS.md D-235.
"""

from __future__ import annotations

import os
import subprocess
import sys
from importlib import metadata
from pathlib import Path

__all__ = ["COMMIT_ENV", "DEPENDENCIES", "code_version", "run_environment"]

COMMIT_ENV = "MERIDIAN_COMMIT"
"""Where an installed copy without ``.git`` is told its commit."""

DEPENDENCIES = ("meridian", "numpy", "scikit-learn", "highspy", "skyfield", "sgp4")
"""The distributions a report's numbers can depend on: ours, the fit and the
solver, and the propagator beneath every pass."""

_GIT_TIMEOUT_S = 10


def run_environment(snapshot: Path) -> dict[str, object]:
    """The environment block for a run built now, from ``snapshot``.

    Args:
        snapshot: The raw snapshot's directory. Recorded as a hint for
            ``verify``, which checks the hash of whatever it finds there.

    Returns:
        Plain values: code, Python, dependency versions and the snapshot path.
    """
    return {
        "code": code_version(),
        "python": sys.version.split()[0],
        "dependencies": {name: _installed(name) for name in DEPENDENCIES},
        "snapshot_path": str(snapshot.resolve()),
    }


def code_version() -> dict[str, object]:
    """The commit this code is, where it came from, and whether it was edited."""
    here = Path(__file__).resolve().parent
    commit = _git(here, "rev-parse", "HEAD")
    if commit is not None:
        changes = _git(here, "status", "--porcelain")
        return {"commit": commit, "dirty": bool(changes), "source": "git"}
    stated = os.environ.get(COMMIT_ENV, "").strip()
    if stated:
        return {"commit": stated, "dirty": None, "source": COMMIT_ENV}
    return {"commit": None, "dirty": None, "source": "unknown"}


def _git(where: Path, *args: str) -> str | None:
    """``git``'s output in ``where``, or None when there is no git or no repo."""
    try:
        done = subprocess.run(
            ["git", "-C", str(where), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def _installed(name: str) -> str | None:
    """A distribution's installed version, or None when it is not installed."""
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None
