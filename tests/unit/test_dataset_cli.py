"""``meridian dataset`` — export a package, verify it, and refuse what is not one.

Run in process through ``meridian.cli.main``, against raw snapshots published
by hand (``tests/unit/conftest.py``), with every database connection and
socket refused: a package is made from a snapshot and nothing else.

What a package promises (Stage 30's tests, SC-10's method):
- two exports from the same inputs are byte-identical;
- one changed row changes the hash;
- simulated receptions are absent unless requested, and apart when they are;
- ``verify`` says an untouched package regenerates (0), refuses a modified
  file (3), and catches one rewritten with its manifest (1);
- a snapshot that moved is found by name, and the wrong one is refused.

Reference: docs/DECISIONS.md D-104, D-143, D-310 to D-315; EVALUATION.md §11.5.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from meridian.cli import main
from meridian.cli_snapshot import EXIT_CORRUPT
from meridian.datasets.canonical import canonical_line
from meridian.datasets.manifest import content_sha256, parse_manifest
from meridian.datasets.publish import read_directory

EXIT_FAILED = 1
EXAMPLE = Path(__file__).resolve().parents[2] / "analysis/configs/dataset.toml.example"


@pytest.fixture
def guarded(no_network: Any, network_guard: Any) -> Any:
    network_guard()
    return no_network


@pytest.fixture
def snapshot(evidence_snapshot: Callable[[Any], Path], evidence_world: Any) -> Path:
    return evidence_snapshot(evidence_world)


def dataset(root: Path, *args: str) -> int:
    return main(["dataset", "--root", str(root), *args])


def export(
    root: Path, snapshot: Path, *extra: str, config: Path = EXAMPLE, seed: str = "4471"
) -> int:
    return dataset(
        root,
        "export",
        "--snapshot",
        str(snapshot),
        "--config",
        str(config),
        "--seed",
        seed,
        *extra,
    )


def package_path(printed: str) -> Path:
    """The directory the first line of an export names."""
    first = printed.splitlines()[0]
    return Path(first.split(": ", 1)[1].rsplit(" (", 1)[0])


def both(tmp_path: Path) -> Path:
    path = tmp_path / "both.toml"
    path.write_text('[export]\npopulations = ["measured", "simulated"]\n', "utf-8")
    return path


def test_a_package_is_exported_from_a_snapshot_alone_and_verifies(
    snapshot: Path,
    datasets_root: Path,
    guarded: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert export(datasets_root, snapshot) == 0
    package = package_path(capsys.readouterr().out)

    assert dataset(datasets_root, "verify", str(package)) == 0
    assert "regenerates identically" in capsys.readouterr().out
    assert guarded.attempts == []
    held = read_directory(package)
    assert held.manifest.kind == "evidence_dataset"
    assert set(held.files) == {
        "config.toml",
        "datasheet.md",
        "licence.txt",
        "receptions.jsonl",
    }
    assert held.files["config.toml"] == EXAMPLE.read_bytes()
    assert held.manifest.parameters["licence"] == "CC-BY-4.0"
    assert held.manifest.derived_from == content_sha256(
        read_directory(snapshot).manifest
    )


def test_two_exports_from_the_same_inputs_are_byte_identical(
    snapshot: Path, datasets_root: Path, tmp_path: Path
) -> None:
    first, second = tmp_path / "first", tmp_path / "second"

    assert export(datasets_root, snapshot, "--output", str(first)) == 0
    assert export(datasets_root, snapshot, "--output", str(second)) == 0

    a, b = read_directory(first), read_directory(second)
    assert dict(a.files) == dict(b.files)
    assert content_sha256(a.manifest) == content_sha256(b.manifest)
    for name, data in a.files.items():
        assert hashlib.sha256(data).digest() == hashlib.sha256(b.files[name]).digest()


def test_exporting_twice_names_one_directory(
    snapshot: Path, datasets_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert export(datasets_root, snapshot) == 0
    first = capsys.readouterr().out
    assert export(datasets_root, snapshot) == 0
    second = capsys.readouterr().out

    assert package_path(first) == package_path(second)
    assert "(written)" in first
    assert "already held, identically" in second


def test_one_changed_row_changes_the_hash(
    evidence_snapshot: Callable[[Any], Path],
    evidence_world: Any,
    datasets_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert export(datasets_root, evidence_snapshot(evidence_world)) == 0
    original = package_path(capsys.readouterr().out)
    evidence_world["reception_verdicts"][1]["probability_usable"] = 0.82

    assert export(datasets_root, evidence_snapshot(evidence_world)) == 0
    changed = package_path(capsys.readouterr().out)

    assert changed != original
    assert content_sha256(read_directory(changed).manifest) != content_sha256(
        read_directory(original).manifest
    )


def test_simulated_receptions_are_absent_unless_requested(
    snapshot: Path,
    datasets_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert export(datasets_root, snapshot) == 0
    measured = read_directory(package_path(capsys.readouterr().out))
    assert export(datasets_root, snapshot, config=both(tmp_path)) == 0
    asked = read_directory(package_path(capsys.readouterr().out))

    assert "receptions_simulated.jsonl" not in measured.files
    assert b"st_sim" not in b"".join(measured.files.values())
    simulated = [
        json.loads(line)
        for line in asked.files["receptions_simulated.jsonl"].splitlines()
    ]
    assert [one["station_id"] for one in simulated] == ["st_sim"]
    assert all(one["simulated"] is True for one in simulated)
    assert b"st_sim" not in asked.files["receptions.jsonl"]
    assert asked.manifest.counts == {
        "receptions.measured": 2,
        "receptions.simulated": 1,
    }


def test_the_datasheet_and_licence_are_part_of_what_regenerates(
    snapshot: Path, datasets_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert export(datasets_root, snapshot) == 0
    held = read_directory(package_path(capsys.readouterr().out))
    sheet = held.files["datasheet.md"].decode("utf-8")

    assert "| measured | `receptions.jsonl` | 2 |" in sheet
    assert content_sha256(read_directory(snapshot).manifest).hex() in sheet
    assert "CC BY 4.0" in held.files["licence.txt"].decode("utf-8")
    assert "meridian dataset verify" in sheet


def test_verify_says_a_modified_file_is_corrupt(
    snapshot: Path, datasets_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert export(datasets_root, snapshot) == 0
    package = package_path(capsys.readouterr().out)
    target = package / "receptions.jsonl"
    package.chmod(0o755)
    target.chmod(0o644)
    target.write_bytes(target.read_bytes().replace(b'"decoded"', b'"no_signal"', 1))

    assert dataset(datasets_root, "verify", str(package)) == EXIT_CORRUPT


def test_verify_notices_a_package_rewritten_with_its_manifest(
    snapshot: Path, datasets_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The positive control: a package re-sealed after an edit still fails."""
    assert export(datasets_root, snapshot) == 0
    package = package_path(capsys.readouterr().out)
    forged = _reseal(package, "receptions.jsonl", b'"decoded"', b'"no_signal"')

    assert dataset(datasets_root, "verify", str(forged)) == EXIT_FAILED
    assert "files that differ: receptions.jsonl" in capsys.readouterr().err


def test_verify_finds_a_snapshot_that_moved_by_its_hash(
    snapshot: Path,
    datasets_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert export(datasets_root, snapshot) == 0
    package = package_path(capsys.readouterr().out)
    moved = tmp_path / "elsewhere"
    shutil.copytree(snapshot, moved)
    _remove(snapshot)

    assert dataset(datasets_root, "verify", str(package)) == EXIT_FAILED
    assert "--snapshot" in capsys.readouterr().err
    assert dataset(datasets_root, "verify", str(package), "--snapshot", str(moved)) == 0


def test_verify_refuses_a_snapshot_that_is_not_the_packages(
    snapshot: Path,
    evidence_snapshot: Callable[[Any], Path],
    evidence_world: Any,
    datasets_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert export(datasets_root, snapshot) == 0
    package = package_path(capsys.readouterr().out)
    evidence_world["assignments"][0]["mode"] = "hrpt"
    other = evidence_snapshot(evidence_world)

    code = dataset(datasets_root, "verify", str(package), "--snapshot", str(other))

    assert code == EXIT_FAILED
    assert "this package was built from" in capsys.readouterr().err


@pytest.mark.parametrize("seed", ["-1", str(2**32)])
def test_a_seed_out_of_range_is_refused(
    snapshot: Path, datasets_root: Path, seed: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert export(datasets_root, snapshot, seed=seed) == EXIT_FAILED
    assert "the seed must be" in capsys.readouterr().err


def test_a_package_is_built_only_from_a_raw_snapshot(
    snapshot: Path, datasets_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert export(datasets_root, snapshot) == 0
    package = package_path(capsys.readouterr().out)

    assert export(datasets_root, package) == EXIT_FAILED
    assert "built from a raw snapshot" in capsys.readouterr().err
    assert dataset(datasets_root, "verify", str(snapshot)) == EXIT_FAILED
    assert "not an evidence dataset" in capsys.readouterr().err


def test_every_row_is_canonical_and_ordered_by_assignment(
    snapshot: Path,
    datasets_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert export(datasets_root, snapshot, config=both(tmp_path)) == 0
    held = read_directory(package_path(capsys.readouterr().out))

    for name in ("receptions.jsonl", "receptions_simulated.jsonl"):
        lines = held.files[name].splitlines(keepends=True)
        rows = [json.loads(line) for line in lines]
        assert [canonical_line(one) for one in rows] == lines
        assert [one["assignment_id"] for one in rows] == sorted(
            one["assignment_id"] for one in rows
        )


def _remove(path: Path) -> None:
    """Delete a sealed directory, which is read-only by design."""
    for one in [path, *path.rglob("*")]:
        one.chmod(0o700 if one.is_dir() else 0o600)
    shutil.rmtree(path)


def _reseal(package: Path, name: str, old: bytes, new: bytes) -> Path:
    """A copy of ``package`` with one file edited and its manifest rewritten."""
    from meridian.datasets.manifest import file_entry, manifest_bytes
    from meridian.datasets.publish import publish_directory

    held = read_directory(package)
    files = dict(held.files)
    files[name] = files[name].replace(old, new, 1)
    assert files[name] != held.files[name]
    manifest = held.manifest.__class__(
        **{
            **{
                field: getattr(held.manifest, field)
                for field in held.manifest.__dataclass_fields__
            },
            "files": tuple(
                file_entry(one, data) for one, data in sorted(files.items())
            ),
        }
    )
    assert parse_manifest(manifest_bytes(manifest))
    return publish_directory(package.parent, "forged", manifest, files).path
