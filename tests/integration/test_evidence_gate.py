"""Stage 30's gate, the half on the machine that makes the package.

*SC-10: regenerating the package from the same snapshot, configuration and
seed, on a machine other than the one that produced it, gives the identical
content hash.*

A faulted fleet flies against the real application in a database of its own,
through the harness SC-8's runs are made with (``deploy/tools/diagnosis_runs.py``),
and is classified and diagnosed as the jobs service would. The database is then
exported as a raw snapshot (Stage 15), and the package built from it. The gate
reads it from every side:

* **it holds the fleet's receptions**, each with its observation or none, and the
  diagnoses of its losses;
* **built again from the same snapshot it is byte-identical**, and ``meridian
  dataset verify`` says it regenerates;
* **every row is simulated, and in the simulated file**: the fleet is virtual,
  so a package asked only for measured receptions holds none.

The other half is CI's ``evidence-elsewhere`` job. When
``MERIDIAN_EVIDENCE_OUT`` names a directory, this test copies the snapshot and
the package into it; that job verifies the package from them on another runner,
in a clean container with no network.

Reference: docs/SOFTWARE-IMPLEMENTATION-ROADMAP.md Stage 30's completion gate;
docs/DECISIONS.md D-310 to D-315; EVALUATION.md §11.5.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from meridian.cli import main  # noqa: E402
from meridian.datasets.evidence_config import parse_evidence_config  # noqa: E402
from meridian.datasets.evidence_dataset import (  # noqa: E402
    build_evidence_dataset,
    publish_evidence_dataset,
)
from meridian.datasets.export import export_snapshot, read_snapshot  # noqa: E402
from meridian.datasets.manifest import content_sha256  # noqa: E402
from meridian.datasets.publish import SnapshotDirectory, read_directory  # noqa: E402
from meridian.registry import psycopg_registry  # noqa: E402
from meridian.registry.psycopg_registry import PsycopgRegistry  # noqa: E402
from meridian.reliability.config import ReliabilityConfig  # noqa: E402

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]
CONFIG = ReliabilityConfig()
PLAN = {
    "scenario": "diagnosis",
    "master_seed": 4472,
    "stations": 4,
    "start": datetime(2026, 8, 12, tzinfo=UTC),
    "hours": 12.0,
    "silent_satellite": "norad:57166",
}
"""Stage 27's pinned fleet: some passes decode, and a dead receiver loses others."""

BOTH = b'[export]\npopulations = ["measured", "simulated"]\n'
SEED = 4471
CREATED = datetime(2026, 10, 29, 9, tzinfo=UTC)
OUT_ENV = "MERIDIAN_EVIDENCE_OUT"


@pytest.fixture(scope="module")
def harness() -> Iterator[ModuleType]:
    """The tool SC-8's runs are made with, imported as running it does."""
    sys.path.insert(0, str(REPO / "deploy" / "migrations"))
    spec = importlib.util.spec_from_file_location(
        "diagnosis_runs", REPO / "deploy" / "tools" / "diagnosis_runs.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["diagnosis_runs"] = module
    spec.loader.exec_module(module)
    try:
        yield module
    finally:
        sys.modules.pop("diagnosis_runs", None)


@pytest.fixture(scope="module")
def exported(
    harness: ModuleType, database_url: str, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[tuple[Path, SnapshotDirectory, SnapshotDirectory]]:
    """The fleet flown, settled and snapshotted, and a package built from it."""
    plan = harness.Plan(**PLAN)
    root = tmp_path_factory.mktemp("datasets")
    ids = harness.station_id_for(plan.master_seed)
    original = psycopg_registry.generate_station_id
    psycopg_registry.generate_station_id = lambda: next(ids)
    try:
        with (
            harness.scratch(database_url, plan.master_seed) as scratch_url,
            psycopg.connect(scratch_url) as conn,
        ):
            harness.fly(conn, plan, state_dir=root / "work")
            harness.settle(conn, plan, CONFIG)
            conn.commit()
            registry = PsycopgRegistry(
                conn,
                pepper=harness.PEPPER,
                recovery_window_s=3600,
                now_utc=plan.end + timedelta(days=2),
            )
            with conn.transaction():
                read = read_snapshot(
                    conn, registry, since=plan.start - timedelta(days=1)
                )
            snapshot = export_snapshot(read, root=root, created_at=CREATED)
    finally:
        psycopg_registry.generate_station_id = original
    raw = read_directory(snapshot.path)
    package = publish_evidence_dataset(
        build_evidence_dataset(
            raw, parse_evidence_config(BOTH), seed=SEED, created_at=CREATED
        ),
        root=root,
    )
    _hand_over(snapshot.path, package.path)
    yield root, raw, read_directory(package.path)


def rows_of(package: SnapshotDirectory, name: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in package.files[name].splitlines()]


def test_the_package_holds_the_fleets_receptions_and_their_diagnoses(
    exported: tuple[Path, SnapshotDirectory, SnapshotDirectory],
) -> None:
    _, _, package = exported

    rows = rows_of(package, "receptions_simulated.jsonl")

    assert rows, "the fleet left no reception"
    assert any(one["observation"] is not None for one in rows)
    assert any(one["diagnoses"] for one in rows), "the fleet's losses were not in it"
    assert all(
        one["observation"] is None
        or one["lineage"][-1]["revision"] == one["observation"]["revision"]
        for one in rows
    )


def test_built_again_from_the_same_snapshot_it_is_byte_identical(
    exported: tuple[Path, SnapshotDirectory, SnapshotDirectory],
) -> None:
    _, raw, package = exported

    again = build_evidence_dataset(
        raw, parse_evidence_config(BOTH), seed=SEED, created_at=CREATED
    )

    assert dict(again.files) == dict(package.files)
    assert content_sha256(again.manifest) == content_sha256(package.manifest)


def test_verify_says_it_regenerates(
    exported: tuple[Path, SnapshotDirectory, SnapshotDirectory],
    capsys: pytest.CaptureFixture[str],
) -> None:
    root, _, package = exported

    assert main(["dataset", "--root", str(root), "verify", str(package.path)]) == 0
    assert "regenerates identically" in capsys.readouterr().out


def test_every_row_is_simulated_and_in_the_simulated_file(
    exported: tuple[Path, SnapshotDirectory, SnapshotDirectory],
) -> None:
    _, raw, package = exported

    assert package.files["receptions.jsonl"] == b""
    assert all(
        one["simulated"] for one in rows_of(package, "receptions_simulated.jsonl")
    )
    measured_only = build_evidence_dataset(
        raw, parse_evidence_config(b""), seed=SEED, created_at=CREATED
    )
    assert measured_only.files["receptions.jsonl"] == b""
    assert "receptions_simulated.jsonl" not in measured_only.files


def _hand_over(snapshot: Path, package: Path) -> None:
    """Copy both for CI's other runner, when it asked for them."""
    named = os.environ.get(OUT_ENV, "").strip()
    if not named:
        return
    out = Path(named)
    for kind, path in (("snapshots", snapshot), ("evidence", package)):
        (out / kind).mkdir(parents=True, exist_ok=True)
        shutil.copytree(path, out / kind / path.name, dirs_exist_ok=True)
