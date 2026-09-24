"""``meridian snapshot`` — its verbs, its refusals and its exit codes.

Run in process through ``meridian.cli.main``, against raw snapshots published
by hand (``tests/unit/conftest.py``), so nothing here needs a database except
the one test that proves ``export`` refuses cleanly without one.

The exit codes are asserted as values because they are what a script reads:
**3** has to mean "this snapshot is not what its manifest says", and nothing
else.

Reference: docs/DECISIONS.md D-143, D-144.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from meridian.cli import main
from meridian.cli_snapshot import DATASETS_ROOT_ENV, EXIT_CORRUPT

EXIT_FAILED = 1


def run(root: Path, *args: str) -> int:
    return main(["snapshot", "--root", str(root), *args])


def dataset_path(printed: str) -> str:
    """The directory the first line of a run's report names."""
    first = printed.splitlines()[0]
    return first.split(": ", 1)[1].rsplit(" (", 1)[0]


# --- label -----------------------------------------------------------------


def test_labelling_twice_names_one_directory(
    raw_snapshot: Any,
    world: Any,
    datasets_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The gate at a prompt: run it twice and read the name."""
    raw = raw_snapshot(world)

    assert run(datasets_root, "label", str(raw)) == 0
    first = capsys.readouterr().out
    assert run(datasets_root, "label", str(raw)) == 0
    second = capsys.readouterr().out

    assert dataset_path(first) == dataset_path(second)
    assert "(written)" in first
    assert "already held, identically" in second


def test_a_label_run_reports_how_much_it_could_not_judge(
    raw_snapshot: Any,
    world: Any,
    datasets_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """EVALUATION.md §5 asks for the indeterminate fraction to be stated."""
    run(datasets_root, "label", str(raw_snapshot(world)))

    printed = capsys.readouterr().out

    assert "indeterminate" in printed
    assert "0 of 1 measured confirmed silences" in printed
    assert "labels.confirmed_miss.measured" in printed


def test_a_configuration_that_cannot_be_obeyed_is_refused(
    raw_snapshot: Any,
    world: Any,
    datasets_root: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = tmp_path / "snapshot.toml"
    config.write_text("settle_margin = 60\n", encoding="utf-8")

    code = run(
        datasets_root, "label", str(raw_snapshot(world)), "--config", str(config)
    )

    assert code == EXIT_FAILED
    said = capsys.readouterr().err
    assert "unknown labelling settings" in said
    assert "Traceback" not in said


def test_labelling_a_damaged_snapshot_exits_3(
    raw_snapshot: Any, world: Any, datasets_root: Path
) -> None:
    raw = raw_snapshot(world)
    raw.chmod(0o700)
    (raw / "stray.jsonl").write_bytes(b"")

    assert run(datasets_root, "label", str(raw)) == EXIT_CORRUPT


def test_labelling_a_path_that_is_not_there_exits_1_not_3(
    datasets_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A typo is not tampering: 3 is kept for a snapshot that has changed."""
    code = run(datasets_root, "label", str(tmp_path / "typo"))

    assert code == EXIT_FAILED
    assert "is not a directory" in capsys.readouterr().err


def test_a_root_that_cannot_be_written_is_refused_with_a_sentence(
    raw_snapshot: Any,
    world: Any,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    blocked = tmp_path / "a-file"
    blocked.write_text("not a directory\n", encoding="utf-8")

    code = run(blocked, "label", str(raw_snapshot(world)))

    assert code == EXIT_FAILED
    assert "Traceback" not in capsys.readouterr().err


def test_the_root_can_come_from_the_environment(
    raw_snapshot: Any,
    world: Any,
    datasets_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(DATASETS_ROOT_ENV, str(datasets_root))

    assert main(["snapshot", "label", str(raw_snapshot(world))]) == 0
    assert str(datasets_root / "evaluation") in capsys.readouterr().out


# --- completeness ------------------------------------------------------------


def labelled(root: Path, raw: Path, capsys: pytest.CaptureFixture[str]) -> str:
    assert run(root, "label", str(raw)) == 0
    return dataset_path(capsys.readouterr().out)


def test_completeness_prints_both_populations(
    raw_snapshot: Any,
    archive_world: Any,
    datasets_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    dataset = labelled(datasets_root, raw_snapshot(archive_world), capsys)

    assert run(datasets_root, "completeness", dataset) == 0
    printed = capsys.readouterr().out
    assert "our stations" in printed
    assert "archive stations" in printed
    assert "threshold          0.8" in printed
    assert "UNRELIABLE" in printed


def test_completeness_at_another_threshold(
    raw_snapshot: Any,
    archive_world: Any,
    datasets_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    dataset = labelled(datasets_root, raw_snapshot(archive_world), capsys)

    assert run(datasets_root, "completeness", dataset, "--threshold", "0.6") == 0
    printed = capsys.readouterr().out
    assert "threshold          0.6" in printed
    assert "retained 2 · below_threshold 0" in printed


@pytest.mark.parametrize("threshold", ["1.5", "-0.1"])
def test_completeness_refuses_a_threshold_off_the_scale(
    raw_snapshot: Any,
    archive_world: Any,
    datasets_root: Path,
    capsys: pytest.CaptureFixture[str],
    threshold: str,
) -> None:
    dataset = labelled(datasets_root, raw_snapshot(archive_world), capsys)

    assert (
        run(datasets_root, "completeness", dataset, "--threshold", threshold)
        == EXIT_FAILED
    )
    assert "outside 0..1" in capsys.readouterr().err


def test_completeness_of_a_raw_snapshot_exits_1(
    raw_snapshot: Any,
    world: Any,
    datasets_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    raw = raw_snapshot(world)

    assert run(datasets_root, "completeness", str(raw)) == EXIT_FAILED
    assert "label it first" in capsys.readouterr().err


def test_completeness_of_a_damaged_dataset_exits_3(
    raw_snapshot: Any,
    archive_world: Any,
    datasets_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    dataset = Path(labelled(datasets_root, raw_snapshot(archive_world), capsys))
    days = dataset / "station_days.jsonl"
    days.chmod(0o600)
    days.write_bytes(days.read_bytes().replace(b'"attempted":2', b'"attempted":3'))

    assert run(datasets_root, "completeness", str(dataset)) == EXIT_CORRUPT


def test_completeness_of_nothing_exits_1(datasets_root: Path, tmp_path: Path) -> None:
    assert run(datasets_root, "completeness", str(tmp_path / "absent")) == EXIT_FAILED


# --- verify ------------------------------------------------------------------


def test_verify_accepts_an_intact_snapshot(
    raw_snapshot: Any,
    world: Any,
    datasets_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert run(datasets_root, "verify", str(raw_snapshot(world))) == 0
    assert "is intact: a raw snapshot of 14 files" in capsys.readouterr().out


def test_verify_exits_3_on_a_changed_byte(
    raw_snapshot: Any,
    world: Any,
    datasets_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    raw = raw_snapshot(world)
    passes = raw / "passes.jsonl"
    passes.chmod(0o600)
    passes.write_bytes(passes.read_bytes().replace(b"st_a", b"st_z"))

    assert run(datasets_root, "verify", str(raw)) == EXIT_CORRUPT
    assert "digest" in capsys.readouterr().err


def test_verify_of_something_that_is_not_there_exits_1(
    datasets_root: Path, tmp_path: Path
) -> None:
    assert run(datasets_root, "verify", str(tmp_path / "absent")) == EXIT_FAILED


# --- export ------------------------------------------------------------------


def test_export_without_a_database_fails_cleanly(
    datasets_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Port 1 is reserved and nothing listens on it, as elsewhere in the suite."""
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql://meridian:meridian@127.0.0.1:1/meridian"
    )

    code = run(datasets_root, "export", "--since", "2026-08-01T00:00:00Z")

    assert code == EXIT_FAILED
    said = capsys.readouterr().err
    assert "cannot reach the database" in said
    assert "Traceback" not in said


@pytest.mark.parametrize(
    ("since", "reason"),
    [("last tuesday", "not an ISO-8601 time"), ("2026-08-01T00:00:00", "needs a zone")],
)
def test_export_refuses_a_start_it_cannot_read(
    since: str, reason: str, datasets_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(datasets_root, "export", "--since", since) == EXIT_FAILED
    assert reason in capsys.readouterr().err


def test_snapshot_without_an_action_shows_its_help(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as raised:
        main(["snapshot"])

    assert raised.value.code == 0
    assert "export" in capsys.readouterr().out
