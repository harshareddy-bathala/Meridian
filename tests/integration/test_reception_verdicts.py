"""``reception_verdicts`` — every closed reception scored once per method.

Marked ``integration`` by the directory hook. What only a database shows: the
writer finds what is unscored, writes one row each, writes nothing again, lets
a new method append, copies ``simulated``, and computes the same inputs hash a
raw snapshot of the same rows gives (D-261).

Reference: docs/DECISIONS.md D-104, D-145, D-261, D-263.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from meridian.datasets.export import export_snapshot, read_snapshot  # noqa: E402
from meridian.datasets.publish import read_directory  # noqa: E402
from meridian.prediction.verdict_inputs import (  # noqa: E402
    FEATURES,
    FULL,
    OUTCOME,
    SNR,
    inputs_sha256,
)
from meridian.prediction.verdict_rows import read_receptions  # noqa: E402
from meridian.prediction.verdict_score import parse_verdict_model  # noqa: E402
from meridian.registry import ListeningQuery  # noqa: E402
from meridian.verdict_build import apply_verdicts  # noqa: E402

pytestmark = pytest.mark.integration

SINCE = datetime(2026, 8, 1, tzinfo=UTC)
CREATED = datetime(2026, 9, 23, 7, 0, tzinfo=UTC)
CLOSED = datetime(2026, 8, 14, 9, 0, tzinfo=UTC)
METHOD = "verdict-1:0123456789ab"


class Listening:
    """Confirms every assignment but ``as_quiet``, and remembers the questions."""

    def __init__(self) -> None:
        self.asked: list[ListeningQuery] = []

    def was_listening(self, query: ListeningQuery) -> bool:
        self.asked.append(query)
        return query.window[0] != CLOSED + timedelta(hours=2)


def model(method: str = METHOD) -> Any:
    def linear(route: str, intercept: float) -> dict[str, object]:
        names = list(FEATURES[route])
        return {
            "features": names,
            "mean": [0.0] * len(names),
            "scale": [1.0] * len(names),
            "coefficients": [0.5] * len(names),
            "intercept": intercept,
            "calibration": {"method": "platt", "a": 1.0, "b": 0.0},
        }

    document = {
        "verdict_format": 1,
        "method": method,
        "rubric": "usable-1",
        "partial_below": 0.4,
        "routes": {
            FULL: linear(FULL, -2.0),
            SNR: linear(SNR, -1.0),
            OUTCOME: linear(OUTCOME, -3.0),
        },
    }
    return parse_verdict_model(json.dumps(document).encode())


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def root(tmp_path: Path) -> Iterator[Path]:
    yield tmp_path
    for path in sorted(tmp_path.rglob("*"), reverse=True):
        path.chmod(0o700 if path.is_dir() else 0o600)


@pytest.fixture
def world(rollback: Any, schedule_rows: Any) -> Any:
    """Five closed receptions, one still open, and one skipped decision.

    ``as_full`` decoded with decoder statistics; ``as_msp02`` decoded without
    them; ``as_quiet`` heard nothing and the registry did not confirm it;
    ``as_sim`` is a simulated station's; ``as_twice`` was resubmitted. The
    downlink has a frame interval, so frames expected is known.
    """
    measured = schedule_rows.station("st_verdict", simulated=False)
    simulated = schedule_rows.station("st_vsim", simulated=True)
    element_set = schedule_rows.satellite()
    rollback.execute(
        "insert into satellite_transmitters (satellite_id, centre_freq_hz, mode,"
        " frame_interval_s) values ('norad:99970', 137900000, 'lrpt', 0.113778)"
    )
    for hour, (name, station) in enumerate(
        (
            ("as_full", measured),
            ("as_msp02", measured),
            ("as_quiet", measured),
            ("as_sim", simulated),
            ("as_twice", measured),
        )
    ):
        pass_id = schedule_rows.pass_(
            station, CLOSED + timedelta(hours=hour), element_set_id=element_set
        )
        schedule_rows.assignment(name, pass_id)
    as_of = rollback.execute("select now()").fetchone()[0]
    open_pass = schedule_rows.pass_(
        measured, as_of - timedelta(minutes=1), element_set_id=element_set
    )
    schedule_rows.assignment("as_open", open_pass)
    for name in ("as_full", "as_msp02", "as_sim", "as_twice", "as_open"):
        schedule_rows.observation(name)
    schedule_rows.observation("as_twice", revision=2)
    schedule_rows.observation("as_quiet", outcome="no_signal")
    rollback.execute(
        "update observations set decoder = 'satdump', decoder_version = '1.2.2',"
        " frames_decoded = 4000, frames_failed = 100"
        " where assignment_id in ('as_full', 'as_sim', 'as_twice')"
    )
    rollback.execute(
        "update observations set peak_snr_db = null where assignment_id = 'as_quiet'"
    )
    return rollback


def stored(conn: Any) -> list[tuple[Any, ...]]:
    return conn.execute(
        "select assignment_id, revision, method, route, simulated, partial_below"
        " from reception_verdicts order by assignment_id, revision, method"
    ).fetchall()


def test_every_closed_reception_gets_one_verdict(world: Any) -> None:
    report = apply_verdicts(world, Listening(), model(), now=CREATED)

    assert report.scored == report.written == 6
    assert stored(world) == [
        ("as_full", 1, METHOD, FULL, False, 0.4),
        ("as_msp02", 1, METHOD, SNR, False, 0.4),
        ("as_quiet", 1, METHOD, OUTCOME, False, 0.4),
        ("as_sim", 1, METHOD, FULL, True, 0.4),
        ("as_twice", 1, METHOD, FULL, False, 0.4),
        ("as_twice", 2, METHOD, FULL, False, 0.4),
    ]
    assert report.simulated == 1
    assert report.routes == {FULL: 4, OUTCOME: 1, SNR: 1}


def test_a_reception_that_heard_nothing_still_has_a_verdict(world: Any) -> None:
    apply_verdicts(world, Listening(), model(), now=CREATED)

    (probability,) = world.execute(
        "select probability_usable from reception_verdicts"
        " where assignment_id = 'as_quiet'"
    ).fetchone()
    assert 0.0 < probability < 0.5


def test_a_window_still_open_is_left_for_later(world: Any) -> None:
    listening = Listening()

    apply_verdicts(world, listening, model(), now=CREATED)

    assert "as_open" not in {row[0] for row in stored(world)}
    assert all(one.window[1] <= CREATED for one in listening.asked)


def test_a_rerun_writes_nothing(world: Any) -> None:
    apply_verdicts(world, Listening(), model(), now=CREATED)

    again = apply_verdicts(world, Listening(), model(), now=CREATED)

    assert again.scored == again.written == 0


def test_a_new_method_appends_beside_the_old(world: Any) -> None:
    apply_verdicts(world, Listening(), model(), now=CREATED)

    newer = apply_verdicts(
        world, Listening(), model("verdict-1:fedcba987654"), now=CREATED
    )

    assert newer.written == 6
    assert len(stored(world)) == 12


def test_a_batch_takes_the_oldest_first(world: Any) -> None:
    first = apply_verdicts(world, Listening(), model(), now=CREATED, limit=2)

    assert first.written == 2
    assert [row[0] for row in stored(world)] == ["as_full", "as_msp02"]


def test_the_live_hash_is_the_hash_a_snapshot_gives(world: Any, root: Path) -> None:
    """D-261: the writer and the snapshot reader read the same nine inputs."""
    listening = Listening()
    apply_verdicts(world, listening, model(), now=CREATED)
    live = {
        (row[0], row[1]): bytes(row[2])
        for row in world.execute(
            "select assignment_id, revision, inputs_sha256 from reception_verdicts"
        ).fetchall()
    }

    published = export_snapshot(
        read_snapshot(world, listening, since=SINCE), root=root, created_at=CREATED
    )
    files = read_directory(published.path).files
    recomputed = {
        (one.assignment_id, one.revision): inputs_sha256(one.inputs)
        for one in read_receptions(files)
    }

    assert live
    assert set(recomputed) == set(live)
    for key, digest in live.items():
        assert recomputed[key] == digest, key
    exported = [json.loads(one) for one in files["reception_verdicts.jsonl"].split()]
    assert {(one["assignment_id"], one["revision"]) for one in exported} == set(live)
    assert published.manifest.counts["reception_verdicts.simulated"] == 1


def test_the_frames_expected_came_from_the_interval(world: Any, root: Path) -> None:
    listening = Listening()
    published = export_snapshot(
        read_snapshot(world, listening, since=SINCE), root=root, created_at=CREATED
    )
    by_id = {
        one.assignment_id: one
        for one in read_receptions(read_directory(published.path).files)
    }

    assert by_id["as_full"].inputs.frames_expected == 5800
    assert by_id["as_quiet"].inputs.listening_confirmed is False
