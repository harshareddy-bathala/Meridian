"""``meridian report`` — build a run, verify it, and refuse what cannot be one.

Run in process through ``meridian.cli.main``, against raw snapshots published
by hand (``tests/unit/conftest.py``), with every database connection and
socket refused: a report is computed from a snapshot and nothing else.

The exit codes are asserted as values because they are what a script reads:
**0** from ``verify`` means the run regenerated identically, **1** that it did
not or could not be checked, and **3** that a directory is not what its
manifest says.

Reference: docs/DECISIONS.md D-234 to D-236.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from meridian.cli import main
from meridian.cli_snapshot import EXIT_CORRUPT
from meridian.datasets.manifest import parse_manifest
from meridian.datasets.publish import read_directory

EXIT_FAILED = 1
EXAMPLE = (
    Path(__file__).resolve().parents[2] / "analysis/configs/evaluation.toml.example"
)


@pytest.fixture
def guarded(no_network: Any, network_guard: Any) -> Any:
    network_guard()
    return no_network


@pytest.fixture
def snapshot(raw_snapshot: Any, archive_world: Any) -> Path:
    path: Path = raw_snapshot(archive_world)
    return path


def report(root: Path, *args: str) -> int:
    return main(["report", "--root", str(root), *args])


def build(root: Path, snapshot: Path, *extra: str, seed: str = "4471") -> int:
    return report(
        root,
        "build",
        "--snapshot",
        str(snapshot),
        "--config",
        str(EXAMPLE),
        "--seed",
        seed,
        *extra,
    )


def run_path(printed: str) -> Path:
    """The directory the first line of a build names."""
    first = printed.splitlines()[0]
    return Path(first.split(": ", 1)[1].rsplit(" (", 1)[0])


def test_a_run_is_built_from_a_snapshot_alone_and_verifies(
    raw_snapshot: Any,
    archive_world: Any,
    datasets_root: Path,
    guarded: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    snapshot = raw_snapshot(archive_world)

    assert build(datasets_root, snapshot) == 0
    run = run_path(capsys.readouterr().out)
    assert report(datasets_root, "verify", str(run)) == 0

    assert "regenerates identically" in capsys.readouterr().out
    assert guarded.attempts == []
    names = {one.name for one in read_directory(run).manifest.files}
    assert names == {
        "config.toml",
        "data.jsonl",
        "prediction.jsonl",
        "report.md",
        "run.jsonl",
        "scheduling.jsonl",
    }


def test_building_twice_names_one_directory(
    raw_snapshot: Any,
    archive_world: Any,
    datasets_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    snapshot = raw_snapshot(archive_world)

    assert build(datasets_root, snapshot) == 0
    first = capsys.readouterr().out
    assert build(datasets_root, snapshot) == 0
    second = capsys.readouterr().out

    assert run_path(first) == run_path(second)
    assert "(written)" in first
    assert "already held, identically" in second


def test_the_run_keeps_its_configuration_byte_for_byte_and_its_seed(
    raw_snapshot: Any, archive_world: Any, datasets_root: Path, capsys: Any
) -> None:
    assert build(datasets_root, raw_snapshot(archive_world), seed="9") == 0
    run = read_directory(run_path(capsys.readouterr().out))

    assert run.files["config.toml"] == EXAMPLE.read_bytes()
    assert run.manifest.parameters["seed"] == 9
    assert run.manifest.kind == "evaluation_report"


def test_the_environment_is_recorded_and_changes_nothing_hashed(
    raw_snapshot: Any, archive_world: Any, datasets_root: Path, capsys: Any
) -> None:
    assert build(datasets_root, raw_snapshot(archive_world)) == 0
    run = run_path(capsys.readouterr().out)
    environment = parse_manifest((run / "manifest.json").read_bytes()).environment

    code = environment["code"]
    assert set(environment) >= {"code", "python", "dependencies", "runtime_s"}
    assert "meridian" in environment["dependencies"]  # type: ignore[operator]
    assert isinstance(code, dict)
    if code["commit"]:
        assert code["commit"].encode() not in (run / "report.md").read_bytes()


def test_an_output_path_is_honoured(
    raw_snapshot: Any, archive_world: Any, datasets_root: Path, tmp_path: Path
) -> None:
    output = tmp_path / "runs" / "run-001"

    assert (
        build(datasets_root, raw_snapshot(archive_world), "--output", str(output)) == 0
    )
    assert (output / "report.md").is_file()


def test_an_output_holding_another_run_is_never_overwritten(
    raw_snapshot: Any, archive_world: Any, datasets_root: Path, tmp_path: Path
) -> None:
    output = tmp_path / "run-001"
    snapshot = raw_snapshot(archive_world)
    assert build(datasets_root, snapshot, "--output", str(output)) == 0

    refused = build(datasets_root, snapshot, "--output", str(output), seed="5")

    assert refused == EXIT_FAILED
    assert read_directory(output).manifest.parameters["seed"] == 4471


def test_verify_finds_a_snapshot_that_moved_by_its_hash(
    raw_snapshot: Any,
    archive_world: Any,
    datasets_root: Path,
    tmp_path: Path,
    capsys: Any,
) -> None:
    snapshot = raw_snapshot(archive_world)
    assert build(datasets_root, snapshot) == 0
    run = run_path(capsys.readouterr().out)
    moved = tmp_path / "elsewhere"
    shutil.copytree(snapshot, moved)
    _remove(snapshot)

    assert report(datasets_root, "verify", str(run)) == EXIT_FAILED
    assert "--snapshot" in capsys.readouterr().err
    assert report(datasets_root, "verify", str(run), "--snapshot", str(moved)) == 0


def test_verify_refuses_a_snapshot_that_is_not_the_runs(
    raw_snapshot: Any,
    archive_world: Any,
    world: Any,
    datasets_root: Path,
    capsys: Any,
) -> None:
    assert build(datasets_root, raw_snapshot(archive_world)) == 0
    run = run_path(capsys.readouterr().out)
    other = raw_snapshot(world)

    assert report(datasets_root, "verify", str(run), "--snapshot", str(other)) == 1
    assert "this run was built from" in capsys.readouterr().err


def test_verify_says_a_tampered_run_is_corrupt(
    raw_snapshot: Any, archive_world: Any, datasets_root: Path, capsys: Any
) -> None:
    assert build(datasets_root, raw_snapshot(archive_world)) == 0
    run = run_path(capsys.readouterr().out)
    target = run / "data.jsonl"
    run.chmod(0o755)
    target.chmod(0o644)
    target.write_bytes(target.read_bytes().replace(b'"measured":1', b'"measured":7'))

    assert report(datasets_root, "verify", str(run)) == EXIT_CORRUPT


def test_verify_notices_a_run_whose_numbers_were_rewritten_consistently(
    raw_snapshot: Any, archive_world: Any, datasets_root: Path, capsys: Any
) -> None:
    """The positive control: a run rewritten *with* its manifest still fails.

    Editing a results file and re-sealing the manifest makes a directory that
    verifies as whole, so only regenerating it can catch the change.
    """
    assert build(datasets_root, raw_snapshot(archive_world)) == 0
    run = run_path(capsys.readouterr().out)
    forged = _reseal(run, "data.jsonl", b'"measured":1', b'"measured":7')

    assert report(datasets_root, "verify", str(forged)) == EXIT_FAILED
    assert "files that differ: data.jsonl" in capsys.readouterr().err


def test_verify_refuses_a_directory_that_is_not_a_run(
    raw_snapshot: Any, archive_world: Any, datasets_root: Path, capsys: Any
) -> None:
    snapshot = raw_snapshot(archive_world)

    assert report(datasets_root, "verify", str(snapshot)) == EXIT_FAILED
    assert "not a report" in capsys.readouterr().err


@pytest.mark.parametrize(
    "case",
    [
        ("seed = 4471\n", "given with --seed"),
        ("[weather]\n", "unknown tables"),
        ("[labels]\nsettle_margin_s = -1\n", "[labels]"),
    ],
)
def test_a_configuration_it_cannot_use_is_refused(
    snapshot: Path,
    datasets_root: Path,
    tmp_path: Path,
    capsys: Any,
    case: tuple[str, str],
) -> None:
    config, reason = case
    path = tmp_path / "evaluation.toml"
    path.write_text(config, encoding="utf-8")

    code = report(
        datasets_root,
        "build",
        "--snapshot",
        str(snapshot),
        "--config",
        str(path),
        "--seed",
        "1",
    )

    assert code == EXIT_FAILED
    assert reason in capsys.readouterr().err


def test_a_seed_out_of_range_is_refused(
    raw_snapshot: Any, archive_world: Any, datasets_root: Path, capsys: Any
) -> None:
    assert build(datasets_root, raw_snapshot(archive_world), seed="-1") == EXIT_FAILED
    assert "--seed must be" in capsys.readouterr().err


def test_building_from_something_other_than_a_raw_snapshot_is_refused(
    raw_snapshot: Any, archive_world: Any, datasets_root: Path, capsys: Any
) -> None:
    assert build(datasets_root, raw_snapshot(archive_world)) == 0
    run = run_path(capsys.readouterr().out)

    assert build(datasets_root, run) == EXIT_FAILED
    assert "not a raw snapshot" in capsys.readouterr().err


def _remove(path: Path) -> None:
    """Delete a sealed directory, which is read-only by design."""
    for one in [path, *path.rglob("*")]:
        one.chmod(0o700 if one.is_dir() else 0o600)
    shutil.rmtree(path)


def _reseal(run: Path, name: str, old: bytes, new: bytes) -> Path:
    """A copy of ``run`` with one file edited and its manifest rewritten to match."""
    from meridian.datasets.manifest import file_entry, manifest_bytes
    from meridian.datasets.publish import publish_directory

    held = read_directory(run)
    files = dict(held.files)
    files[name] = files[name].replace(old, new)
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
    assert json.loads(manifest_bytes(manifest))["content_sha256"]
    return publish_directory(run.parent, "forged", manifest, files).path
