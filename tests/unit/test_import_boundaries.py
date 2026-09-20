"""Each distribution imports only what its own boundary allows.

ARCHITECTURE.md: the station client knows nothing about the database. The
client's dependency list keeps psycopg off a station's disk
(tests/unit/test_layout.py), but a dependency list cannot stop a source file
importing ``meridian`` — the platform package — which is installed beside it in
every development checkout and in the image. An import like that works
everywhere tests run and fails only on a Pi, which is exactly where nobody is
watching.

So every import statement in every distribution's ``src`` is read from the
source and checked against that distribution's own list. **One list per
distribution rather than one list for all of them**, because the boundaries are
not the same shape:

* ``platform`` may not reach *outward* into the client, the simulator or the
  archive layer. It is depended upon; it depends on none of them.
* ``client`` and ``simulator`` may not reach the platform or a database. The
  simulator is held to the client's rule because it drives the real client
  against a platform over MSP, and a shortcut into the platform's modules would
  make it test something no station can do. The client also never imports the
  simulator, which depends on it and not the other way round.
* ``ingest`` **may** import ``meridian`` — that is its whole purpose, and D-138
  makes the arrow one-way rather than absent — but not a database driver, and
  not its sibling distributions. The SQL lives in ``meridian.store``, whose
  ``__init__`` states that only that package talks to the database; a second
  SQL dialect in a second distribution would be a second set of conventions to
  keep aligned with one schema.

A flat list cannot express that last case: ``meridian`` and ``psycopg`` are
banned everywhere in it, and ingest needs the first.

Reference: docs/ARCHITECTURE.md; docs/DECISIONS.md D-012, D-120, D-138.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

DATABASE_AND_WEB = ("psycopg", "fastapi", "pydantic", "sqlalchemy", "alembic")
"""The platform's own dependencies. Reachable from any distribution that depends
on ``meridian``, which is why importing one has to be checked rather than
prevented by a dependency list."""

FORBIDDEN: dict[str, tuple[str, ...]] = {
    "platform": ("meridian_client", "meridian_sim", "meridian_ingest"),
    "client": ("meridian", "meridian_sim", "meridian_ingest", *DATABASE_AND_WEB),
    "simulator": ("meridian", "meridian_ingest", *DATABASE_AND_WEB),
    "ingest": ("meridian_client", "meridian_sim", *DATABASE_AND_WEB),
}
"""Top-level packages each distribution may not import.

``meridian`` is the platform; ``meridian_client``, ``meridian_sim`` and
``meridian_ingest`` are different top-level names and are not matched by it — so
each is listed where it is banned. ``simulator`` may import ``meridian_client``
and is the only one that may: it is a client implementation, not a mock.
"""


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


@pytest.mark.parametrize("distribution", sorted(FORBIDDEN))
def test_the_distribution_imports_nothing_across_its_boundary(
    distribution: str,
) -> None:
    forbidden = FORBIDDEN[distribution]
    crossings = [
        f"{path.relative_to(REPO_ROOT)}:{line} imports {package}"
        for path in sources(distribution)
        for line, package in imported_top_levels(path)
        if package in forbidden
    ]

    assert crossings == []


def test_every_distribution_is_covered() -> None:
    """A member added to the workspace gets a boundary, or fails here.

    The mapping above is written by hand, so the failure it invites is a fifth
    distribution that nothing checks — which looks exactly like a distribution
    with no crossings.
    """
    import tomllib

    root = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert sorted(root["tool"]["uv"]["workspace"]["members"]) == sorted(FORBIDDEN)


def test_the_scan_would_notice_a_crossing(tmp_path: Path) -> None:
    """A scan that finds nothing must be able to find something."""
    offender = tmp_path / "offender.py"
    offender.write_text(
        "import os\nfrom meridian.store.pool import open_pool\nimport psycopg.rows\n",
        encoding="utf-8",
    )

    found = [package for _, package in imported_top_levels(offender)]

    assert found == ["os", "meridian", "psycopg"]
    assert "meridian" in FORBIDDEN["client"]
    assert "psycopg" in FORBIDDEN["ingest"]
