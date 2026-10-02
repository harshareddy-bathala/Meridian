"""Stage 19's gate, from the source and the documents.

    Every deferred table has an active producer, consumer, provenance policy,
    migration test, and retention decision.

Each clause is checked for each table by reading what was written, not by
trusting a list:

* **producer** — a store module inserts into it, and a runtime module calls
  that store module;
* **consumer** — a store module selects from it, and an API route, a command or
  the snapshot export calls that module;
* **provenance** — its definition carries ``simulated``, and a learned or
  derived one names the method and dataset that made it;
* **migration test** — ``test_migrations.py`` writes rows into it;
* **retention** — its section of ``DATA-MODEL.md`` states a retention, and no
  migration drops anything on a timer.

Every check has a positive control: a table the roadmap plans and nobody has
built fails it, so a check that passes has not passed by seeing nothing.

The database half is ``tests/integration/test_deferred_storage_gate.py``.

Marked as a unit test by living in ``tests/unit``.

Reference: docs/DECISIONS.md D-173 to D-178; docs/SOFTWARE-IMPLEMENTATION-ROADMAP.md
Stage 19.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PLATFORM = REPO_ROOT / "platform" / "src" / "meridian"
MIGRATIONS = REPO_ROOT / "deploy" / "migrations" / "sql"
DATA_MODEL = REPO_ROOT / "docs" / "DATA-MODEL.md"
MIGRATION_TESTS = REPO_ROOT / "tests" / "integration" / "test_migrations.py"

RUNTIME_CALLERS = ("api", "cli", "datasets", "jobs", "observations", "profile_build")
"""Where a producer or consumer is called from: a route, a command, the export,
the jobs service, ingest, or the profile build."""


@dataclass(frozen=True, slots=True)
class Gate:
    """Where one table's producer and consumer are expected, and what it records."""

    producer: str
    """The store module that writes it."""

    consumers: tuple[str, ...]
    """The store modules that read it."""

    lineage: tuple[str, ...] = ()
    """Columns that say what made a row, beyond ``simulated``."""


DEFERRED = {
    "noise_measurements": Gate(
        "store/noise_measurements.py",
        ("store/snapshot_reads.py",),
        ("source", "assignment_id", "revision"),
    ),
    "products": Gate(
        "store/products.py",
        ("store/observation_history.py", "store/snapshot_reads.py"),
        ("assignment_id", "revision", "sha256"),
    ),
    "horizon_profiles": Gate(
        "store/profiles.py",
        ("store/profiles.py",),
        ("source", "method", "dataset_sha256", "capability_id"),
    ),
    "interference_profiles": Gate(
        "store/profiles.py",
        ("store/profiles.py",),
        ("method", "dataset_sha256"),
    ),
}

PLANNED = "signal_baselines"
"""A table the roadmap plans for Stage 28 and nothing builds yet: the control.
Stage 27 built `loss_diagnoses`, and Stage 26 `reception_verdicts`, which were
the control before it."""


def _module_name(relative: str) -> str:
    return "meridian." + relative.removesuffix(".py").replace("/", ".")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


def _called_at_runtime(relative: str) -> list[str]:
    """The runtime modules that import one store module."""
    module = _module_name(relative)
    callers = []
    for path in sorted(PLATFORM.rglob("*.py")):
        named = path.relative_to(PLATFORM).as_posix()
        if named == relative or not named.startswith(RUNTIME_CALLERS):
            continue
        if module in _imports(path):
            callers.append(named)
    return callers


def _source(relative: str) -> str:
    return (PLATFORM / relative).read_text(encoding="utf-8").lower()


def _writes(relative: str, table: str) -> bool:
    return re.search(rf"insert into {table}\b", _source(relative)) is not None


def _reads(relative: str, table: str) -> bool:
    return re.search(rf"(?:from|join) {table}\b", _source(relative)) is not None


def _definition(table: str) -> str:
    """The ``create table`` or ``create materialized view`` block, lower-cased."""
    for path in sorted(MIGRATIONS.glob("*.sql")):
        sql = path.read_text(encoding="utf-8").lower()
        found = re.search(
            rf"create (?:table|materialized view) {table}\b(.*?)\n\);?\n", sql, re.S
        )
        if found:
            return found.group(1)
    return ""


def _data_model_section(table: str) -> str:
    text = DATA_MODEL.read_text(encoding="utf-8")
    found = re.search(rf"^### `{table}`.*?(?=^### |^## )", text, re.S | re.M)
    return "" if found is None else found.group(0)


# --- producer -----------------------------------------------------------------


@pytest.mark.parametrize("table", DEFERRED)
def test_a_producer_writes_it_and_is_called(table: str) -> None:
    gate = DEFERRED[table]

    assert _writes(gate.producer, table), gate.producer
    assert _called_at_runtime(gate.producer), f"nothing calls {gate.producer}"


def test_the_producer_check_sees_a_table_nobody_writes() -> None:
    """Positive control: no module writes the planned table."""
    writers = [
        path
        for path in PLATFORM.rglob("*.py")
        if f"insert into {PLANNED}" in path.read_text(encoding="utf-8").lower()
    ]

    assert writers == []


# --- consumer -----------------------------------------------------------------


@pytest.mark.parametrize("table", DEFERRED)
def test_a_consumer_reads_it_and_is_called(table: str) -> None:
    for consumer in DEFERRED[table].consumers:
        assert _reads(consumer, table), consumer
        assert _called_at_runtime(consumer), f"nothing calls {consumer}"


def test_the_aggregate_is_read_by_the_uptime_route() -> None:
    assert _reads("store/heartbeat_coverage.py", "heartbeats_hourly")
    assert "api/public/stations.py" in _called_at_runtime("store/heartbeat_coverage.py")


def test_the_caller_check_sees_a_module_nothing_imports() -> None:
    """Positive control: a module no runtime path imports is reported as such."""
    assert _called_at_runtime("store/__nothing_by_this_name__.py") == []


# --- provenance ---------------------------------------------------------------


@pytest.mark.parametrize("table", DEFERRED)
def test_its_rows_say_what_made_them(table: str) -> None:
    definition = _definition(table)

    assert re.search(r"\bsimulated\s+boolean\s+not null\b", definition), table
    for column in DEFERRED[table].lineage:
        assert re.search(rf"^\s+{column}\s", definition, re.M), (table, column)


def test_the_aggregate_keeps_the_simulated_flag() -> None:
    assert "simulated" in _definition("heartbeats_hourly")


def test_the_definition_check_finds_nothing_for_a_planned_table() -> None:
    assert _definition(PLANNED) == ""


# --- migration test -----------------------------------------------------------


@pytest.mark.parametrize("table", DEFERRED)
def test_a_migration_test_writes_rows_into_it(table: str) -> None:
    tests = MIGRATION_TESTS.read_text(encoding="utf-8")

    assert f"insert into {table}" in tests, table


def test_the_migration_test_check_sees_a_planned_table_is_untested() -> None:
    assert f"insert into {PLANNED}" not in MIGRATION_TESTS.read_text(encoding="utf-8")


# --- retention ----------------------------------------------------------------


@pytest.mark.parametrize("table", [*DEFERRED, "heartbeats"])
def test_its_retention_is_decided_in_the_data_model(table: str) -> None:
    section = _data_model_section(table)

    assert section, table
    assert re.search(r"\*\*Retention:\*\*|Retention: \*\*none", section), table


def test_the_retention_check_sees_a_planned_table_without_one() -> None:
    section = _data_model_section(PLANNED)

    assert section
    assert "Retention" not in section


def test_no_migration_drops_rows_on_a_timer() -> None:
    """D-178: nothing that holds evidence has a retention policy."""
    for path in sorted(MIGRATIONS.glob("*.sql")):
        assert "add_retention_policy" not in path.read_text(encoding="utf-8"), path
