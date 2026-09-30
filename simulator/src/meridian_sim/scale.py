"""What a fleet saw of the platform under load, round by round.

Stage 21 runs one, five, ten and fifty stations and asks how the platform bears
it. The platform's side of that — request latency, the pool, the solver, series
counts — is read from its own ``/metrics`` by ``deploy/tools/scale_probe.py``.
This is the fleet's side, which only the fleet can see:

* **heartbeats answered per second**, against the rate the fleet should send;
* **rounds that overran** the cadence, which is the fleet itself falling
  behind rather than the platform;
* **each station's upload queue**, the observations on its disk not yet
  acknowledged, sampled every round — a queue that grows under load is the
  platform failing to keep up in a way no platform metric shows.

A :class:`ScaleRecorder` is handed to :meth:`Supervisor.run
<meridian_sim.supervisor.Supervisor.run>` as its observer and writes a JSON
report at the end. Simulated, and every report says so (CLAUDE.md rule 5).

Reference: docs/SOFTWARE-IMPLEMENTATION-ROADMAP.md Stage 21; docs/DECISIONS.md
D-197.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from meridian_sim.config import RunConfig
from meridian_sim.supervisor import RoundOutcome
from meridian_sim.virtual_station import paths_for

__all__ = ["ScaleRecorder", "queue_depth"]

REPORT_FORMAT = "meridian-sim-scale/1"


def queue_depth(outbox: Path) -> int:
    """How many observations wait in one station's upload queue."""
    if not outbox.is_dir():
        return 0
    return sum(1 for one in outbox.iterdir() if one.is_file())


@dataclass
class ScaleRecorder:
    """Accumulates each round of a run, and writes the report.

    Args:
        config: The run being observed; its station count and state directory
            say where each station's queue is.
        interval_s: The cadence the fleet was asked to keep, for judging a round
            as overrun.
    """

    config: RunConfig
    interval_s: float
    rounds: int = 0
    heard: int = 0
    ticked: int = 0
    submitted: int = 0
    overran: int = 0
    seconds: float = 0.0
    longest_round_s: float = 0.0
    queue_by_round: list[int] = field(default_factory=list)
    """The fleet's total queued observations after each round."""

    deepest_queue: int = 0
    """The deepest any one station's queue got, at any round."""

    clock: Callable[[], float] = time.monotonic
    """Seconds, monotonic; a test passes its own."""

    first_start: float | None = None
    last_start: float | None = None

    def __call__(self, outcome: RoundOutcome, took_s: float) -> None:
        """Record one round. A :data:`~meridian_sim.supervisor.RoundObserver`."""
        began = self.clock() - took_s
        if self.first_start is None:
            self.first_start = began
        self.last_start = began
        self.rounds += 1
        self.heard += len(outcome.heard)
        self.ticked += len(outcome.ticked)
        self.submitted += len(outcome.submitted)
        self.seconds += took_s
        self.longest_round_s = max(self.longest_round_s, took_s)
        if took_s > self.interval_s:
            self.overran += 1
        depths = [
            queue_depth(paths_for(self.config, index).outbox)
            for index in range(1, self.config.station_count + 1)
        ]
        self.queue_by_round.append(sum(depths))
        self.deepest_queue = max(self.deepest_queue, max(depths, default=0))

    def report(self) -> dict[str, object]:
        """The run, as the JSON the report file holds."""
        wall_s = self.window_s()
        return {
            "format": REPORT_FORMAT,
            "simulated": True,
            "run_id": self.config.run_id,
            "master_seed": self.config.master_seed,
            "scenario": self.config.scenario,
            "stations": self.config.station_count,
            "interval_s": self.interval_s,
            "rounds": self.rounds,
            "heartbeats_answered": self.heard,
            "heartbeats_attempted": self.ticked,
            "heartbeats_answered_per_s": round(self.heard / wall_s, 3)
            if wall_s
            else 0.0,
            "heartbeats_expected_per_s": round(
                self.config.station_count / self.interval_s, 3
            ),
            "observations_acknowledged": self.submitted,
            "rounds_overran": self.overran,
            "longest_round_s": round(self.longest_round_s, 3),
            "mean_round_s": round(self.seconds / self.rounds, 3)
            if self.rounds
            else 0.0,
            "queue_total_final": self.queue_by_round[-1] if self.queue_by_round else 0,
            "queue_total_max": max(self.queue_by_round, default=0),
            "queue_deepest_station": self.deepest_queue,
        }

    def window_s(self) -> float:
        """Seconds from the first round starting to a cadence past the last.

        Measured, not ``rounds * interval``: a supervisor that overruns skips
        the rounds it missed (D-076), so under exactly the load this report
        exists to measure, the rounds span longer than the cadence says, and
        a rate over the nominal span would read faster than it was.
        """
        if self.first_start is None or self.last_start is None:
            return 0.0
        return self.last_start - self.first_start + self.interval_s

    def write(self, path: Path) -> None:
        """Write the report, creating its directory if it has none."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.report(), indent=2) + "\n", encoding="utf-8")
