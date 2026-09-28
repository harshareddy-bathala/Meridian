"""Stage 18's gate, end to end: fit, schedule, explain, compare.

The roadmap states it as: *the optimized scheduler always emits a
constraint-valid schedule, records its reasoning, and can be compared
reproducibly against baselines and an oracle.* Each clause is asserted here,
through ``meridian snapshot label``, ``meridian model fit`` and ``meridian
schedule evaluate`` wherever a command reaches it, over a raw snapshot built
in this file. The database half of "records its reasoning" — every stored
decision names its run and explains itself — is
``tests/integration/test_scheduler_gate.py``.

The world is 22 days over two stations. Each station has eight passes a day of
one satellite, and once a day a second satellite rises six minutes after one
of them, so one antenna must choose. The historical policy took the higher of
the two, so every station-day is 8 of 9 attempted, which the dataset's own
threshold of 0.8 keeps.

**Each claim has its positive control**, since a test that cannot fail proves
nothing: the solver's wrong answer is wrong, a schedule nobody checked stops
the comparison, a decision without its terms cannot be explained, the report
moves when its seed or its data does, and the oracle is above some scheduler
somewhere.

Reference: docs/DECISIONS.md D-165 to D-172; docs/EVALUATION.md §3.
"""

from __future__ import annotations

import os
import random
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from meridian.cli import main
from meridian.prediction.replay import Replay, load_replay
from meridian.prediction.score import sigmoid
from meridian.scheduler import (
    Candidate,
    Commitment,
    ScheduleOutcome,
    ScoredCandidate,
)
from meridian.scheduler import optimiser as optimiser_module
from meridian.scheduler import replay as replay_module
from meridian.scheduler.constraints import Problem, Rules, violations
from meridian.scheduler.explanations import RunFacts, explain
from meridian.scheduler.objective import modelled, value_candidates
from meridian.scheduler.optimiser import SolverSettings, optimise
from meridian.scheduler.programme import Answer
from meridian.scheduler.replay import (
    ORACLE,
    SCHEDULERS,
    candidate_of,
    replay_schedules,
)
from meridian.scheduler.schedule_config import ScheduleConfig

SINCE = datetime(2026, 9, 1, tzinfo=UTC)
DAYS = 22
DAILY = "norad:57166"
RIVAL = "norad:59051"
STATIONS = ("st_a", "st_b")
CLASH_SLOT = 2
"""The slot a rival pass rises beside, every day."""

SETTINGS = (
    "min_station_history = 5\n"
    "folds = 0\n"
    "train_until = 2026-09-11T00:00:00Z\n"
    "validate_until = 2026-09-16T00:00:00Z\n"
)

FRESH = (
    sys.executable,
    "-c",
    "import sys; from meridian.cli import main; sys.exit(main(sys.argv[1:]))",
)

Rows = dict[str, list[Mapping[str, object]]]


def gate_world(reverse: frozenset[int] = frozenset()) -> Rows:
    """The world, with the outcomes of the passes ``reverse`` names flipped."""
    rng = random.Random(18)
    rows: Rows = {"passes": [], "assignments": [], "observations": []}
    pass_id = 0

    def a_pass(station: str, satellite: str, aos: datetime) -> tuple[dict, int]:
        nonlocal pass_id
        pass_id += 1
        elevation = rng.uniform(5.0, 85.0)
        odds = 0.9 if satellite == RIVAL else 1.0
        decoded = rng.random() < odds * sigmoid((elevation - 35.0) / 10.0)
        decoded = decoded != (pass_id in reverse)
        frames = rng.randint(20, 400) if decoded else 0
        element_set = 2 * ((aos - SINCE).days) + (satellite == RIVAL)
        return (
            {
                "id": pass_id,
                "satellite_id": satellite,
                "station_id": station,
                "aos": aos,
                "los": aos + timedelta(minutes=12),
                "max_elevation_deg": elevation,
                "aos_azimuth_deg": rng.uniform(0.0, 360.0),
                "los_azimuth_deg": rng.uniform(0.0, 360.0),
                "element_set_id": element_set,
                "computed_at": aos - timedelta(hours=5),
                "simulated": False,
            },
            frames,
        )

    for day in range(DAYS):
        for offset, station in enumerate(STATIONS):
            for slot in range(8):
                aos = SINCE + timedelta(days=day, hours=3 * slot + offset, minutes=10)
                daily = a_pass(station, DAILY, aos)
                taken = [daily]
                rows["passes"].append(daily[0])
                if slot == CLASH_SLOT:
                    rival = a_pass(station, RIVAL, aos + timedelta(minutes=6))
                    rows["passes"].append(rival[0])
                    higher = max(
                        (daily, rival), key=lambda one: one[0]["max_elevation_deg"]
                    )
                    taken = [higher]
                for predicted, frames in taken:
                    _report(rows, predicted, frames)
    return rows | {
        "element_sets": [
            {
                "id": 2 * day + index,
                "satellite_id": satellite,
                "epoch": SINCE + timedelta(days=day),
            }
            for day in range(DAYS)
            for index, satellite in enumerate((DAILY, RIVAL))
        ],
        "stations": [{"station_id": station, "lon_deg": 77.6} for station in STATIONS],
        "satellites": [
            {"satellite_id": DAILY, "priority": 1.0},
            {"satellite_id": RIVAL, "priority": 1.5},
        ],
        "transmitters": [
            {
                "id": index + 1,
                "satellite_id": one,
                "centre_freq_hz": 137_900_000,
                "active": True,
                "deleted_at": None,
            }
            for index, one in enumerate((DAILY, RIVAL))
        ],
    }


def _report(rows: Rows, predicted: Mapping[str, object], frames: int) -> None:
    number = predicted["id"]
    rows["assignments"].append(
        {
            "assignment_id": f"as_{number}",
            "pass_id": number,
            "station_id": predicted["station_id"],
            "start_at": predicted["aos"],
            "end_at": predicted["los"],
            "decision": "scheduled",
            "state": "reported",
            "model_config": "B",
            "simulated": False,
        }
    )
    rows["observations"].append(
        {
            "assignment_id": f"as_{number}",
            "revision": 1,
            "outcome": "decoded" if frames else "signal_no_decode",
            "first_detection_at": None,
            "noise_floor_dbfs": None,
            "frames_decoded": frames,
            "simulated": False,
        }
    )


def printed_path(out: str) -> Path:
    first = out.splitlines()[0]
    return Path(first.split(": ", 1)[1].rsplit(" (", 1)[0])


class Gate:
    """The operator's commands, run in process against one datasets root."""

    def __init__(
        self, root: Path, raw_snapshot: Any, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self.root = root
        self.raw_snapshot = raw_snapshot
        self.capsys = capsys
        self.dataset = Path()
        self.models: dict[str, Path] = {}

    def run(self, *args: str) -> tuple[int, str, str]:
        self.capsys.readouterr()
        code = main([*args[:1], "--root", str(self.root), *args[1:]])
        captured = self.capsys.readouterr()
        return code, captured.out, captured.err

    def ok(self, *args: str) -> str:
        code, out, err = self.run(*args)
        assert code == 0, err
        return out

    def build(self, reverse: frozenset[int] = frozenset()) -> Gate:
        raw = self.raw_snapshot(gate_world(reverse))
        self.dataset = printed_path(self.ok("snapshot", "label", str(raw)))
        for name in ("A", "C", "D"):
            config = self.root.parent / f"model-{name}.toml"
            config.write_text(f'configuration = "{name}"\n{SETTINGS}', "utf-8")
            self.models[name] = printed_path(
                self.ok("model", "fit", str(self.dataset), "--config", str(config))
            )
        return self

    def config(self, seed: int = 0) -> Path:
        path = self.root.parent / f"schedule-evaluation-{seed}.toml"
        lines = [f"seed = {seed}", "resamples = 300", "[models]"]
        lines += [f'{name} = "{where}"' for name, where in sorted(self.models.items())]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def evaluate(self, seed: int = 0) -> tuple[int, str, str]:
        return self.run(
            "schedule",
            "evaluate",
            str(self.dataset),
            "--config",
            str(self.config(seed)),
        )

    def replay(self) -> Replay:
        return load_replay(self.dataset, self.models, root=self.root)


@pytest.fixture
def gate(
    datasets_root: Path, raw_snapshot: Any, capsys: pytest.CaptureFixture[str]
) -> Gate:
    return Gate(datasets_root, raw_snapshot, capsys).build()


def table(out: str) -> dict[str, list[str]]:
    """The report's scheduler rows, by scheduler: every column after the name."""
    rows = {}
    for line in out.splitlines():
        parts = line.split()
        if parts[:1] == ["greedy"]:
            name, rest = " ".join(parts[:2]), parts[2:]
        else:
            name, rest = (parts[0], parts[1:]) if parts else ("", [])
        if name in SCHEDULERS and rest[:1] and rest[0].isdigit():
            rows[name] = rest
    return rows


def frames_column(out: str) -> dict[str, str]:
    return {name: row[1] for name, row in table(out).items()}


def no_answer(*_: object) -> Answer:
    return Answer(None, "fallback", None, 0.0, "the gate took the answer away")


def every_candidate(free: Sequence[ScoredCandidate], *_: object) -> Answer:
    return Answer(frozenset(range(len(free))), "optimal", 0.0, 0.0, None)


# --- a constraint-valid schedule, always ---------------------------------------


def test_the_comparison_runs_every_scheduler_and_every_schedule_is_valid(
    gate: Gate,
) -> None:
    """``replay_schedules`` checks each schedule with ``violations`` before it
    is counted, so a clean exit is every schedule of every scheduler valid."""
    code, out, err = gate.evaluate()

    rows = table(out)
    assert code == 0, err
    assert sorted(rows) == sorted(SCHEDULERS)
    assert "14 station-days replayed" in out
    for name in ("A", "B", "C", "D", ORACLE):
        assert rows[name][-2:] == ["optimal", "14"], name


@pytest.mark.parametrize(
    ("solver", "solved"),
    [
        (no_answer, ["fallback", "14"]),
        # Every candidate is a valid answer on the two days with no clash.
        (every_candidate, ["fallback", "12", "·", "optimal", "2"]),
    ],
)
def test_a_solver_that_answers_nothing_or_wrongly_still_leaves_a_valid_schedule(
    gate: Gate, monkeypatch: pytest.MonkeyPatch, solver: Any, solved: list[str]
) -> None:
    """No answer, or one taking every candidate: greedy under the same
    constraints takes over, is checked like any schedule, and says so."""
    monkeypatch.setattr(optimiser_module, "solve", solver)

    code, out, err = gate.evaluate()

    rows = table(out)
    assert code == 0, err
    for name in ("A", "B", "C", "D", ORACLE):
        assert rows[name][-len(solved) :] == solved, name


def test_the_wrong_answer_is_wrong(gate: Gate) -> None:
    """Positive control: taking every candidate of a day breaks a rule."""
    replay = gate.replay()
    day = replay.days[0]
    candidates = tuple(candidate_of(replay.passes[one]) for one in day.pass_ids)
    everything = ScheduleOutcome(
        selected=[ScoredCandidate(one, 1.0) for one in candidates], rejected=[]
    )

    broken = violations(
        Problem(candidates, (), frozenset(), Rules(turnaround_s=0.0)), everything
    )

    assert {one.rule for one in broken} == {"overlap"}


def test_a_schedule_nobody_checked_stops_the_comparison(
    gate: Gate, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Positive control: an unchecked greedy baseline taking everything is
    refused with the rule it broke, and no report is printed."""

    def take_everything(ranked: Sequence[Any], **_: object) -> ScheduleOutcome:
        return ScheduleOutcome(selected=list(ranked), rejected=[])

    monkeypatch.setattr(replay_module, "select_without_conflict", take_everything)

    code, out, err = gate.evaluate()

    assert (code, out) == (1, "")
    assert "greedy A broke overlap" in err


def random_problem(seed: int) -> tuple[list[ScoredCandidate], Rules, tuple[Any, ...]]:
    """Up to 25 passes over two stations, some commitments, seeded."""
    rng = random.Random(seed)
    t0 = datetime(2026, 9, 20, tzinfo=UTC)

    def one(pass_id: int, minute: float) -> Candidate:
        aos = t0 + timedelta(minutes=minute)
        return Candidate(
            pass_id=pass_id,
            station_id=rng.choice(STATIONS),
            aos=aos,
            los=aos + timedelta(minutes=rng.uniform(4, 15)),
            margin_s=rng.choice([0.0, 0.5, 30.0]),
            max_elevation_deg=rng.uniform(5, 85),
            priority=1.0,
            simulated=False,
        )

    scored = [
        ScoredCandidate(one(n, rng.uniform(0, 300)), round(rng.uniform(0, 90), 3))
        for n in range(1, rng.randint(1, 25) + 1)
    ]
    committed = tuple(
        Commitment(one(900 + n, rng.uniform(-30, 320)), f"as_c{n}")
        for n in range(rng.randint(0, 2))
    )
    rules = Rules(
        turnaround_s=rng.choice([0.0, 90.0]), most_eligible=rng.choice([2, 8])
    )
    return scored, rules, committed


@pytest.mark.parametrize("solver", [None, no_answer, every_candidate])
def test_on_two_hundred_seeded_problems_every_schedule_is_valid(
    monkeypatch: pytest.MonkeyPatch, solver: Any
) -> None:
    """Checked again here, independently of the check inside ``optimise``."""
    if solver is not None:
        monkeypatch.setattr(optimiser_module, "solve", solver)
    statuses = set()
    for seed in range(200):
        scored, rules, committed = random_problem(seed)
        found = optimise(
            scored,
            rules=rules,
            settings=SolverSettings(time_limit_s=5.0),
            committed=committed,
        )
        problem = Problem(
            tuple(one.candidate for one in scored), committed, frozenset(), rules
        )
        assert violations(problem, found.outcome) == (), seed
        statuses.add(found.run.status)

    if solver is None:
        assert statuses == {"optimal"}
    elif solver is no_answer:
        assert statuses == {"fallback"}
    else:
        # Taking everything is a valid answer where nothing clashes.
        assert statuses == {"fallback", "optimal"}


# --- its reasoning, recorded --------------------------------------------------


def test_every_decision_of_d_explains_itself_from_the_model_s_yield(
    gate: Gate,
) -> None:
    """Each station-day scheduled under D: every pass decided has its terms,
    its value is their product, its yield is the model's probability, and a
    skip names what beat it."""
    replay = gate.replay()
    rules = Rules(turnaround_s=0.0)
    skips = 0
    for day in replay.days:
        candidates = [candidate_of(replay.passes[one]) for one in day.pass_ids]
        yields = {
            one: modelled(p.probability, p.path, p.reason)
            for one, p in replay.predictions["D"].items()
            if one in day.pass_ids
        }
        scored, terms = value_candidates(
            candidates, configuration="D", frames_term="duration", yields=yields
        )
        found = optimise(scored, rules=rules, settings=SolverSettings(10.0))
        explained = explain(
            found.outcome,
            terms,
            (),
            turnaround_s=0.0,
            run=RunFacts(status=found.run.status, history_as_of=None),
        )

        assert sorted(explained) == sorted(day.pass_ids)
        for held in explained.values():
            kept = held["terms"]
            weight = kept["priority"] if kept["priority_weighted"] else 1.0
            product = kept["yield"] * kept["frames"] * weight
            assert kept["value"] == pytest.approx(product)
            assert kept["frames"] == 720.0
        for one in found.outcome.selected:
            held = explained[one.candidate.pass_id]
            assert held["rule"] is None
            assert held["terms"]["value"] == one.score
            assert held["terms"]["yield_source"] == "model"
        for lost in found.outcome.rejected:
            held = explained[lost.scored.candidate.pass_id]
            assert held["rule"] == lost.rule
            assert held["alternative"]["pass_id"] == lost.conflicts_with_pass_id
            skips += 1
    assert skips == sum(1 for one in replay.days if len(one.pass_ids) == 9)
    assert skips > 0


def test_a_decision_without_its_terms_cannot_be_explained(gate: Gate) -> None:
    """Positive control: the explanation is built from the terms, not assumed."""
    replay = gate.replay()
    candidates = [candidate_of(replay.passes[one]) for one in replay.days[0].pass_ids]
    scored = [ScoredCandidate(one, 1.0) for one in candidates]
    found = optimise(scored, rules=Rules(turnaround_s=0.0), settings=SolverSettings(5))

    with pytest.raises(KeyError):
        explain(
            found.outcome,
            {},
            (),
            turnaround_s=0.0,
            run=RunFacts(status="optimal", history_as_of=None),
        )


# --- compared reproducibly, against baselines and an oracle ----------------------


def test_the_comparison_is_the_same_twice_and_in_other_processes(gate: Gate) -> None:
    first = gate.evaluate()
    again = gate.evaluate()
    printed = []
    for seed in ("1", "2"):
        ran = subprocess.run(
            [
                *FRESH,
                "schedule",
                "--root",
                str(gate.root),
                "evaluate",
                str(gate.dataset),
                "--config",
                str(gate.config()),
            ],
            capture_output=True,
            text=True,
            check=True,
            env=os.environ | {"PYTHONHASHSEED": seed},
        )
        printed.append(ran.stdout)

    assert first[0] == 0, first[2]
    assert first == again
    assert printed == [first[1], first[1]]


def test_the_comparison_moves_with_its_seed_and_with_its_data(
    datasets_root: Path, raw_snapshot: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    """Positive control: the same bytes are not the same regardless. Another
    seed moves an interval and not the frames (D − greedy B's, since D and B
    agree here and their interval is 0 at any seed); a flipped outcome moves
    the frames."""
    gate = Gate(datasets_root, raw_snapshot, capsys).build()
    _, seeded_0, _ = gate.evaluate(seed=0)
    _, seeded_1, _ = gate.evaluate(seed=1)
    flipped = Gate(datasets_root, raw_snapshot, capsys).build(frozenset(range(400)))
    _, other_data, _ = flipped.evaluate()

    assert frames_column(seeded_0) == frames_column(seeded_1)
    against_practice = [
        [line for line in out.splitlines() if "D − greedy B" in line]
        for out in (seeded_0, seeded_1)
    ]
    assert against_practice[0] != against_practice[1]
    assert frames_column(other_data)[ORACLE] != frames_column(seeded_0)[ORACLE]


def test_the_oracle_takes_at_least_every_scheduler_s_frames_on_every_day(
    gate: Gate,
) -> None:
    results = replay_schedules(gate.replay(), ScheduleConfig(time_limit_s=10.0))
    oracle = results.results[ORACLE]

    assert {one.status for one in oracle} == {"optimal"}
    for name in SCHEDULERS:
        for mine, bound in zip(results.results[name], oracle, strict=True):
            assert mine.frames <= bound.frames, name
    assert any(
        mine.frames < bound.frames
        for name in SCHEDULERS
        if name != ORACLE
        for mine, bound in zip(results.results[name], oracle, strict=True)
    )
