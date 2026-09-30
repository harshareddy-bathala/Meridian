"""``meridian.reports`` — computed from a snapshot, and imported by its command alone.

Two lines, read from the source rather than from a running import:

* **Nothing in the package can reach a database, a socket or a service.** The
  roadmap's rule is that no report command may silently fetch mutable external
  data; the command tests prove it for the fixtures they run, and this proves it
  for every line, including the ones no fixture reaches. ``DATABASE_URL`` is
  not named either, so no module reads the setting by hand.
* **Nothing but ``cli_report`` imports the package.** A report is evaluation,
  and a runtime path that imported one would be deciding on a report.

Each has a positive control, without which an empty list of crossings proves
nothing.

Reference: docs/DECISIONS.md D-234, D-235.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PLATFORM = REPO / "platform" / "src" / "meridian"
REPORTS = PLATFORM / "reports"

REACHES_OUTSIDE = (
    "psycopg",
    "socket",
    "urllib",
    "http",
    "httpx",
    "requests",
    "meridian.store",
    "meridian.registry",
    "meridian.api",
    "meridian.config",
)
"""A driver, the network, the SQL layer, the registry over it, or the settings
that name a database. A report needs none of them."""


def imports(path: Path) -> Iterator[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.module


def crossings(paths: list[Path], banned: tuple[str, ...]) -> list[str]:
    return [
        f"{path.name} imports {module}"
        for path in paths
        for module in imports(path)
        if any(module == one or module.startswith(one + ".") for one in banned)
    ]


def report_modules() -> list[Path]:
    return sorted(REPORTS.rglob("*.py"))


def test_nothing_in_a_report_can_reach_a_database_or_a_network() -> None:
    assert report_modules()
    assert crossings(report_modules(), REACHES_OUTSIDE) == []


def test_no_report_module_names_the_database_setting() -> None:
    named = [
        path.name
        for path in report_modules()
        if "DATABASE_URL" in path.read_text(encoding="utf-8")
    ]

    assert named == []


def test_only_the_report_command_imports_reports() -> None:
    outside = [
        path
        for path in sorted(PLATFORM.rglob("*.py"))
        if REPORTS not in path.parents and path.name != "cli_report.py"
    ]

    assert crossings(outside, ("meridian.reports",)) == []


def test_the_scans_would_notice_a_crossing(tmp_path: Path) -> None:
    """Positive controls: the command imports reports, and a rogue module is seen."""
    rogue = tmp_path / "rogue.py"
    rogue.write_text("import psycopg\nfrom http import client\n", encoding="utf-8")

    assert crossings([PLATFORM / "cli_report.py"], ("meridian.reports",))
    assert len(crossings([rogue], REACHES_OUTSIDE)) == 2
