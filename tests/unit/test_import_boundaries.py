"""The station client and the simulator never import the platform or a database.

ARCHITECTURE.md: the station client knows nothing about the database. The client's
dependency list keeps psycopg off a station's disk (tests/unit/test_layout.py),
but a dependency list cannot stop a source file importing ``meridian`` — the
platform package — which is installed beside it in every development checkout
and in the image. An import like that works everywhere tests run and fails only
on a Pi, which is exactly where nobody is watching.

So every import statement in ``client/src`` and ``simulator/src`` is read from
the source and checked. The simulator is held to the same rule: it drives the
real client against a platform over MSP, and a shortcut into the platform's
modules would make it test something no station can do. The client also never
imports the simulator, which depends on it, not the other way round.

Reference: docs/ARCHITECTURE.md; docs/DECISIONS.md D-012, D-120.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

FORBIDDEN_EVERYWHERE = (
    "meridian",
    "psycopg",
    "fastapi",
    "pydantic",
    "sqlalchemy",
    "alembic",
)
"""Top-level packages neither distribution may import. ``meridian`` is the
platform; ``meridian_client`` and ``meridian_sim`` are different top-level names
and are not matched by it."""


def imported_top_levels(path: Path) -> Iterator[tuple[int, str]]:
    """Each absolute import in ``path``, as its line and top-level package."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.lineno, node.module.split(".")[0]


def sources(distribution: str) -> list[Path]:
    files = sorted((REPO_ROOT / distribution / "src").rglob("*.py"))
    assert files, f"no sources found under {distribution}/src"
    return files


@pytest.mark.parametrize("distribution", ["client", "simulator"])
def test_the_distribution_imports_no_platform_or_database_package(
    distribution: str,
) -> None:
    crossings = [
        f"{path.relative_to(REPO_ROOT)}:{line} imports {package}"
        for path in sources(distribution)
        for line, package in imported_top_levels(path)
        if package in FORBIDDEN_EVERYWHERE
    ]

    assert crossings == []


def test_the_client_never_imports_the_simulator() -> None:
    crossings = [
        f"{path.relative_to(REPO_ROOT)}:{line}"
        for path in sources("client")
        for line, package in imported_top_levels(path)
        if package == "meridian_sim"
    ]

    assert crossings == []


def test_the_scan_would_notice_a_crossing(tmp_path: Path) -> None:
    """A scan that finds nothing must be able to find something."""
    offender = tmp_path / "offender.py"
    offender.write_text(
        "import os\nfrom meridian.store.pool import open_pool\nimport psycopg.rows\n",
        encoding="utf-8",
    )

    found = [package for _, package in imported_top_levels(offender)]

    assert found == ["os", "meridian", "psycopg"]
