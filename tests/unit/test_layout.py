"""Guards on the repository layout itself.

These test the scaffolding rather than behaviour, because both invariants below
fail in ways that are expensive to diagnose and cheap to prevent.
"""

from __future__ import annotations

import platform as stdlib_platform
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_stdlib_platform_is_not_shadowed() -> None:
    """``import platform`` must resolve to the standard library.

    ``platform/`` is a distribution root, not a package. If it ever gains an
    ``__init__.py`` it shadows the stdlib module for the whole process, and the
    failure surfaces as an AttributeError from inside pip or uvicorn with a
    traceback pointing nowhere near our code. See docs/DECISIONS.md D-012.
    """
    assert stdlib_platform.system() != ""
    assert not (REPO_ROOT / "platform" / "__init__.py").exists()
    assert (REPO_ROOT / "platform" / "src" / "meridian" / "__init__.py").exists()


def test_client_distribution_cannot_reach_the_database() -> None:
    """The reference client's dependency list must not admit database drivers.

    docs/ARCHITECTURE.md says the station client knows nothing about the database.
    The dependency list is what enforces it, so a change to that list should fail
    here rather than pass review unnoticed.
    """
    pyproject = (REPO_ROOT / "client" / "pyproject.toml").read_text(encoding="utf-8")
    dependencies = pyproject.split("dependencies = [")[1].split("]")[0]
    for banned in ("psycopg", "sqlalchemy", "fastapi", 'meridian"'):
        assert banned not in dependencies, f"{banned} must not be a client dependency"


def test_require_utc_rejects_naive_and_offset_datetimes() -> None:
    """Timestamps crossing the orbit boundary are timezone-aware UTC, always."""
    from meridian.orbit import require_utc

    aware = datetime(2026, 8, 14, 9, 41, 20, tzinfo=UTC)
    assert require_utc(aware, "t") is aware

    with pytest.raises(ValueError, match="naive"):
        require_utc(datetime(2026, 8, 14, 9, 41, 20), "t")  # noqa: DTZ001

    ist = timezone(timedelta(hours=5, minutes=30))
    with pytest.raises(ValueError, match="not UTC"):
        require_utc(datetime(2026, 8, 14, 15, 11, 20, tzinfo=ist), "t")


def test_element_set_age_is_measured_from_epoch() -> None:
    """Element-set age is a first-class feature; it must be computed, not guessed."""
    from meridian.orbit import ElementSet

    epoch = datetime(2026, 8, 14, 2, 11, 0, tzinfo=UTC)
    element_set = ElementSet(
        satellite_id="norad:57166",
        epoch=epoch,
        line1="1 ...",
        line2="2 ...",
    )
    assert element_set.age_s(epoch + timedelta(days=6)) == pytest.approx(518400.0)
