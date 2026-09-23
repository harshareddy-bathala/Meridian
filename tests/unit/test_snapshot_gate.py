"""Stage 15's gate: one raw snapshot, one configuration, one dataset hash.

The roadmap states it as: *the same raw snapshot and the same transformation
configuration always produce the same evaluation dataset hash.* Here it is
tested three ways at once — labelled by the library twice at different times,
and once through ``meridian snapshot label`` — each into its own root, with
every network connection refused. The refusal is shown to work by a positive
control: ``export`` under the same guard is caught trying to connect.

The mutation checks say the other half: the hash is not so forgiving that it
would stay put when the input changes. Row order is part of the bytes, so an
export that forgot to sort would be caught, not normalised away.

Reference: docs/DECISIONS.md D-143, D-144.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
import pytest

from meridian.cli import main
from meridian.datasets.evaluation import build_evaluation_dataset
from meridian.datasets.label_config import LabelConfig
from meridian.datasets.manifest import content_sha256
from meridian.datasets.publish import read_directory

EARLY = datetime(2026, 9, 23, 7, 0, tzinfo=UTC)
LATE = datetime(2027, 3, 1, 12, 0, tzinfo=UTC)

LABEL_IN_A_FRESH_PROCESS = (
    "import sys; from meridian.cli import main; sys.exit(main(sys.argv[1:]))"
)


@dataclass
class NetworkGuard:
    """Refuses every database connection and socket, and remembers each attempt."""

    attempts: list[str] = field(default_factory=list)

    def refuse(self, what: str) -> OSError:
        self.attempts.append(what)
        return OSError(f"the gate test has no network; {what} was attempted")


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[NetworkGuard]:
    """Close every door a labelling run could use to reach a database."""
    guard = NetworkGuard()

    def refuse_psycopg(*_args: object, **_kwargs: object) -> None:
        raise guard.refuse("psycopg.connect")

    def refuse_socket(*_args: object, **_kwargs: object) -> None:
        raise guard.refuse("socket.connect")

    monkeypatch.setattr(psycopg, "connect", refuse_psycopg)
    monkeypatch.setattr(psycopg.Connection, "connect", refuse_psycopg)
    monkeypatch.setattr(socket.socket, "connect", refuse_socket)
    monkeypatch.setattr(socket, "create_connection", refuse_socket)
    yield guard


def dataset_hash(path: Path) -> str:
    return content_sha256(read_directory(path).manifest).hex()


def tree(path: Path) -> dict[str, bytes]:
    """Every file in a published directory, by name — the bytes, not the hash."""
    return {one.name: one.read_bytes() for one in sorted(path.iterdir())}


def printed_path(out: str) -> Path:
    first = out.splitlines()[0]
    return Path(first.split(": ", 1)[1].rsplit(" (", 1)[0])


# --- the gate ------------------------------------------------------------------


def test_three_labellings_make_one_dataset(
    raw_snapshot: Any,
    world: Any,
    datasets_root: Path,
    no_network: NetworkGuard,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Twice by the library, months apart, once at the prompt: one hash, one tree."""
    raw = raw_snapshot(world)

    first = build_evaluation_dataset(
        read_directory(raw), LabelConfig(), root=datasets_root / "a", created_at=EARLY
    )
    second = build_evaluation_dataset(
        read_directory(raw), LabelConfig(), root=datasets_root / "b", created_at=LATE
    )
    code = main(["snapshot", "--root", str(datasets_root / "c"), "label", str(raw)])
    third = printed_path(capsys.readouterr().out)

    assert code == 0
    assert no_network.attempts == []
    assert first.written and second.written
    assert dataset_hash(first.path) == dataset_hash(second.path) == dataset_hash(third)
    assert first.path.name == second.path.name == third.name
    labelled = tree(first.path)
    assert {name: labelled[name] for name in labelled if name != "manifest.json"} == {
        name: data for name, data in tree(third).items() if name != "manifest.json"
    }


def test_the_guard_catches_a_run_that_does_reach_for_a_database(
    datasets_root: Path,
    no_network: NetworkGuard,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Positive control: without it, an empty attempt list would prove nothing."""
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql://meridian:meridian@127.0.0.1:5432/meridian"
    )

    code = main(
        [
            "snapshot",
            "--root",
            str(datasets_root),
            "export",
            "--since",
            "2026-09-01T00:00:00Z",
        ]
    )

    assert code == 1
    assert no_network.attempts


def test_the_hash_does_not_depend_on_the_process(
    raw_snapshot: Any, world: Any, datasets_root: Path
) -> None:
    """Two interpreters with different hash seeds, so no set order leaks in."""
    raw = raw_snapshot(world)
    names = []
    for seed, root in (("0", "p"), ("4471", "q")):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                LABEL_IN_A_FRESH_PROCESS,
                "snapshot",
                "--root",
                str(datasets_root / root),
                "label",
                str(raw),
            ],
            capture_output=True,
            text=True,
            check=True,
            env=os.environ | {"PYTHONHASHSEED": seed},
        )
        names.append(printed_path(result.stdout).name)

    assert names[0] == names[1]


# --- mutation checks -------------------------------------------------------------


def label(raw: Path, root: Path) -> str:
    published = build_evaluation_dataset(
        read_directory(raw), LabelConfig(), root=root, created_at=EARLY
    )
    return dataset_hash(published.path)


def with_rows(
    world: Mapping[str, Sequence[Mapping[str, object]]],
    table: str,
    rows: Sequence[Mapping[str, object]],
) -> dict[str, Sequence[Mapping[str, object]]]:
    return dict(world) | {table: rows}


def test_rows_in_another_order_are_another_snapshot(
    raw_snapshot: Any, world: Any, datasets_root: Path
) -> None:
    """Order is in the bytes. Sorting is the export's job, and this is why."""
    sorted_raw = raw_snapshot(world)
    shuffled_raw = raw_snapshot(
        with_rows(world, "passes", list(reversed(world["passes"])))
    )

    assert dataset_hash(sorted_raw) != dataset_hash(shuffled_raw)
    assert label(sorted_raw, datasets_root / "x") != label(
        shuffled_raw, datasets_root / "y"
    )


def test_one_changed_outcome_is_another_dataset(
    raw_snapshot: Any, world: Any, datasets_root: Path
) -> None:
    observations = [dict(one) for one in world["observations"]]
    observations[1]["outcome"] = "decoded"

    original = label(raw_snapshot(world), datasets_root / "x")
    changed = label(
        raw_snapshot(with_rows(world, "observations", observations)),
        datasets_root / "y",
    )

    assert original != changed


def test_one_changed_setting_is_another_dataset(
    raw_snapshot: Any, world: Any, datasets_root: Path
) -> None:
    raw = raw_snapshot(world)

    default = build_evaluation_dataset(
        read_directory(raw), LabelConfig(), root=datasets_root, created_at=EARLY
    )
    wider = build_evaluation_dataset(
        read_directory(raw),
        LabelConfig(silent_window_s=LabelConfig().silent_window_s + 1),
        root=datasets_root,
        created_at=EARLY,
    )

    assert dataset_hash(default.path) != dataset_hash(wider.path)
