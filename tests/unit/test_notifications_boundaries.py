"""What owner reports may import, and who may import them (D-098, D-102).

* **The text is made from the standard library alone.** The facts, the wording
  and the templates import nothing outside it and their own module, so no
  model, no network client and no database can reach a sentence a report says.
* **No language model anywhere in the module** (D-098), by name.
* **Nothing the platform decides with imports notifications.** Scheduling,
  prediction, reliability, the registry, observations and the store never wait
  on a report, so a delivery cannot delay or change what they do (D-102).

Each has a positive control, without which an empty list proves nothing.

Reference: docs/DECISIONS.md D-098, D-102, D-303.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

PLATFORM = Path(__file__).resolve().parents[2] / "platform" / "src" / "meridian"
NOTIFICATIONS = PLATFORM / "notifications"

TEXT_PATH = ("facts.py", "wording.py", "templates.py")
"""Every module a report's words come from."""

LANGUAGE_MODELS = (
    "anthropic",
    "openai",
    "transformers",
    "langchain",
    "llama_cpp",
    "ollama",
    "google.generativeai",
)

DECIDING = (
    "scheduler",
    "prediction",
    "reliability",
    "registry",
    "observations",
    "orbit",
    "store",
)
"""The modules whose work a delivery must never hold up."""


def imports(path: Path) -> list[str]:
    """Every absolute module ``path`` imports, ``from a import b`` read as ``a``."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.append(node.module)
    return found


def outside_the_standard_library(path: Path) -> list[str]:
    return [
        name
        for name in imports(path)
        if name.split(".")[0] not in sys.stdlib_module_names
        and name != "meridian.notifications"
        and not name.startswith("meridian.notifications.")
    ]


def test_a_reports_words_come_from_the_standard_library_alone() -> None:
    for name in TEXT_PATH:
        assert outside_the_standard_library(NOTIFICATIONS / name) == [], name


def test_the_standard_library_check_sees_a_third_party_import() -> None:
    """Positive control: the store module imports psycopg."""
    assert "psycopg.rows" in outside_the_standard_library(
        PLATFORM / "store" / "report_deliveries.py"
    )


def test_no_language_model_is_imported_by_notifications() -> None:
    for path in sorted(NOTIFICATIONS.rglob("*.py")):
        for name in imports(path):
            assert not name.startswith(LANGUAGE_MODELS), f"{path.name}: {name}"


def test_the_language_model_check_matches_by_prefix() -> None:
    assert "openai.types".startswith(LANGUAGE_MODELS)
    assert not "openapi".startswith(LANGUAGE_MODELS)


def importers_of_notifications(module: str) -> list[str]:
    found = []
    for path in sorted((PLATFORM / module).rglob("*.py")):
        if any(
            name == "meridian.notifications"
            or name.startswith("meridian.notifications.")
            for name in imports(path)
        ):
            found.append(path.relative_to(PLATFORM).as_posix())
    return found


def test_nothing_that_decides_imports_notifications() -> None:
    for module in DECIDING:
        assert importers_of_notifications(module) == [], module


def test_the_importer_check_sees_an_importer() -> None:
    """Positive control: the templates import the facts."""
    assert "notifications/templates.py" in importers_of_notifications("notifications")
