"""``meridian_sim.scale`` — the fleet's side of a scale run.

No marker: a round's outcome and a directory of queued files are all it reads.

Reference: docs/DECISIONS.md D-197.
"""

from __future__ import annotations

from pathlib import Path

from meridian_sim.config import RunConfig
from meridian_sim.scale import ScaleRecorder, queue_depth
from meridian_sim.supervisor import RoundOutcome
from meridian_sim.virtual_station import paths_for


def config(tmp_path: Path, count: int = 3) -> RunConfig:
    """A run of ``count`` stations over a temporary state directory."""
    return RunConfig(
        master_seed=4471,
        run_id="scale-run",
        station_count=count,
        base_url="http://platform.test",
        state_dir=tmp_path / "state",
    )


def outcome(tick: int, heard: tuple[int, ...], ticked: tuple[int, ...]) -> RoundOutcome:
    """One round in which ``ticked`` ran and ``heard`` were answered."""
    return RoundOutcome(
        tick=tick,
        ticked=ticked,
        restarted=(),
        stopped=(),
        submitted=("as_1",),
        heard=heard,
    )


def test_a_missing_queue_is_empty(tmp_path: Path) -> None:
    """A station that never queued anything has no outbox yet."""
    assert queue_depth(tmp_path / "nowhere") == 0


def test_the_report_counts_what_the_platform_answered(tmp_path: Path) -> None:
    """Answered against attempted, and per second against the cadence."""
    recorder = ScaleRecorder(config(tmp_path), interval_s=30.0)
    recorder(outcome(0, (1, 2, 3), (1, 2, 3)), 0.5)
    recorder(outcome(1, (1, 2), (1, 2, 3)), 0.5)

    report = recorder.report()

    assert report["simulated"] is True
    assert (report["heartbeats_answered"], report["heartbeats_attempted"]) == (5, 6)
    assert report["heartbeats_answered_per_s"] == round(5 / 60, 3)
    assert report["heartbeats_expected_per_s"] == 0.1
    assert report["observations_acknowledged"] == 2


def test_a_round_longer_than_the_cadence_is_an_overrun(tmp_path: Path) -> None:
    """The fleet falling behind, which is not the platform's doing."""
    recorder = ScaleRecorder(config(tmp_path), interval_s=30.0)
    recorder(outcome(0, (1,), (1,)), 31.0)
    recorder(outcome(1, (1,), (1,)), 2.0)

    assert recorder.report()["rounds_overran"] == 1
    assert recorder.report()["longest_round_s"] == 31.0


def test_queues_are_sampled_every_round(tmp_path: Path) -> None:
    """A queue that grows under load is the platform not keeping up."""
    run = config(tmp_path)
    recorder = ScaleRecorder(run, interval_s=30.0)
    outbox = paths_for(run, 2).outbox
    outbox.mkdir(parents=True)
    recorder(outcome(0, (1,), (1,)), 1.0)
    for name in ("a.json", "b.json"):
        (outbox / name).write_text("{}", encoding="utf-8")
    recorder(outcome(1, (1,), (1,)), 1.0)

    report = recorder.report()

    assert recorder.queue_by_round == [0, 2]
    assert (report["queue_total_max"], report["queue_deepest_station"]) == (2, 2)


def test_the_report_is_written_as_json(tmp_path: Path) -> None:
    """Where the operator asked, with its format named."""
    recorder = ScaleRecorder(config(tmp_path), interval_s=30.0)
    recorder(outcome(0, (1,), (1,)), 1.0)
    path = tmp_path / "out" / "scale.json"

    recorder.write(path)

    assert '"format": "meridian-sim-scale/1"' in path.read_text("utf-8")
