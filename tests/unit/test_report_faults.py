"""Saved fault runs: evidence kept, judged again, and reported with SC-5.

A fault run is two faults, written by hand so every interval is known: a
network outage at one station, whose heartbeats stop at 12:00:00 and resume at
12:10:30, and a pause of the platform's API. The station reads offline 90 s
after its last heartbeat, a round that finished reading at 12:01:50 revokes its
unbegun work at 12:02:00, and ``StationOffline`` fires at 12:02:10.

**Each claim has its positive control:** the evidence read back is the evidence
written; the verdicts reached again from it are the ones published, and a
changed piece of evidence changes them; a run built with the fault run
regenerates, and fails to without it; a tampered fault run is refused; and the
72-hour run is only included from a sealed long run's own record that spans
72 hours and passed, with every fault passing when judged again — a short run,
a failed one and a run without a record each say why not (D-257).

Reference: docs/DECISIONS.md D-189, D-192, D-240, D-257.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from meridian.cli import main
from meridian.cli_snapshot import EXIT_CORRUPT
from meridian.datasets.fault_runs import publish_fault_run, read_fault_run
from meridian.datasets.long_run_record import (
    LongRunRecord,
    LongRunRecordError,
    parse_long_run_record,
)
from meridian.reliability.fault_ledger import InjectedFault
from meridian.reliability.fault_model import (
    Gathered,
    PlatformEvidence,
    StationEvidence,
    StationWork,
)
from meridian.reliability.fault_record import (
    FaultRecordError,
    evidence_rows,
    gathered_from_rows,
)
from meridian.reliability.faults import judge_gathered

T0 = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
SECOND = timedelta(seconds=1)


def ledger(hours: float = 0.25) -> str:
    """The run's ledger: a station outage, and a platform fault ``hours`` long."""
    lines = [
        _line("open", "network_down", "station:1", T0, station_id="st_a"),
        _line("close", "network_down", "station:1", T0 + 600 * SECOND),
        _line("open", "api_paused", "platform:api", T0 + 1200 * SECOND),
        _line("close", "api_paused", "platform:api", T0 + timedelta(hours=hours)),
    ]
    return "\n".join(lines) + "\n"


def _line(event: str, kind: str, target: str, at: datetime, **more: str) -> str:
    return json.dumps(
        {
            "ledger": 1,
            "event": event,
            "run_id": "run-1",
            "kind": kind,
            "target": target,
            "at": at.isoformat().replace("+00:00", "Z"),
        }
        | more
    )


def faults_of(text: str) -> tuple[InjectedFault, ...]:
    import io

    from meridian.reliability.fault_ledger import read_fault_ledger

    return read_fault_ledger(io.StringIO(text))


def gathered(faults: tuple[InjectedFault, ...]) -> tuple[Gathered, ...]:
    station, platform = faults
    beats = (
        *(T0 - n * 30 * SECOND for n in range(10, -1, -1)),
        T0 + 630 * SECOND,
    )
    work = StationWork(
        assignment_id="as_1",
        pass_id=1,
        decided_at=T0 - timedelta(hours=1),
        start_at=T0 + 1200 * SECOND,
        end_at=T0 + 1900 * SECOND,
        state="revoked",
        revoked_reason="offline",
        revoked_at=T0 + 120 * SECOND,
        redecided_at=None,
        offline_revocations=(T0 + 120 * SECOND,),
        revocations=(T0 + 120 * SECOND,),
    )
    evidence = StationEvidence(
        heartbeats=beats,
        work=(work,),
        rounds=(T0 + 100 * SECOND,),
        round_ends={T0 + 100 * SECOND: T0 + 110 * SECOND},
        classifications={"as_1": "station_unavailable"},
        reported=frozenset(),
        as_of=T0 + timedelta(hours=2),
        alert_fired_at=T0 + 130 * SECOND,
        alert_asked=True,
    )
    return (
        Gathered(station, evidence),
        Gathered(
            platform,
            PlatformEvidence(
                first_heartbeat_after=(platform.closed_at or T0) + 20 * SECOND,
                first_round_after=None,
                reported_misses=(),
                as_of=T0 + timedelta(hours=2),
            ),
        ),
    )


def run_record(hours: float, *failures: str) -> LongRunRecord:
    """What ``deploy/tools/long_run.py`` seals of a run ``hours`` long."""
    document = {
        "format": "meridian-long-run/2",
        "simulated": True,
        "started": T0.isoformat(),
        "ended": (T0 + timedelta(hours=hours)).isoformat(),
        "seed": 4471,
        "stations": 10,
        "passed": not failures,
        "failures": list(failures),
        "environment": {
            "arch": "aarch64",
            "image_digests": ["ghcr.io/x/meridian@sha256:" + "ab" * 32],
        },
        "interruptions": [],
        "resources": {
            "figures": {
                "peak_memory_mib": {"api": 233.0},
                "memory_slope_mib_per_hour": {"api": 0.4},
                "database_mib": {"first": 120.0, "last": 340.0},
            },
            "failures": [],
        },
    }
    return parse_long_run_record(json.dumps(document).encode())


def published(
    root: Path, hours: float = 0.25, record: LongRunRecord | None = None
) -> Path:
    text = ledger(hours)
    found = gathered(faults_of(text))
    verdicts = [judge_gathered(one) for one in found]
    as_of = max(T0 + timedelta(hours=2), T0 + timedelta(hours=hours))
    return publish_fault_run(
        text, found, verdicts, root=root, stamp=("0025", as_of), record=record
    ).path


# --- the checks time what they judge (D-240) ---------------------------------


def test_each_timed_check_carries_its_interval() -> None:
    verdict = judge_gathered(gathered(faults_of(ledger()))[0])
    timed = {one.name: one.latency_s for one in verdict.checks}

    assert timed["detected"] == 90.0
    assert timed["replanned"] == 30.0
    assert timed["alerted"] == 130.0
    assert verdict.passed


# --- the record --------------------------------------------------------------


def test_the_evidence_read_back_is_the_evidence_written() -> None:
    faults = faults_of(ledger())
    written = gathered(faults)
    rows = json.loads(json.dumps(evidence_rows(written)))

    assert gathered_from_rows(faults, rows) == written


def test_evidence_that_does_not_match_its_ledger_is_refused() -> None:
    faults = faults_of(ledger())
    rows = json.loads(json.dumps(evidence_rows(gathered(faults))))

    with pytest.raises(FaultRecordError, match="for a ledger of"):
        gathered_from_rows(faults, rows[:1])
    rows[0]["heartbeats"] = ["12:00"]
    with pytest.raises(FaultRecordError):
        gathered_from_rows(faults, rows)


def test_a_published_run_judges_again_to_its_own_verdicts(datasets_root: Path) -> None:
    run = read_fault_run(published(datasets_root))
    again = [judge_gathered(one) for one in run.gathered]

    assert [one.passed for one in again] == [row["passed"] for row in run.verdicts]
    assert run.directory.manifest.kind == "fault_run"
    assert run.directory.manifest.counts["faults"] == 2


def test_changed_evidence_changes_the_verdict(datasets_root: Path) -> None:
    """Positive control: the judges read the evidence, not the stored verdict."""
    run = read_fault_run(published(datasets_root))
    station = run.gathered[0]
    assert isinstance(station.evidence, StationEvidence)
    late = replace(station.evidence, heartbeats=station.evidence.heartbeats[:-1])

    assert judge_gathered(replace(station, evidence=late)) != judge_gathered(station)


# --- the report --------------------------------------------------------------


def build(root: Path, snapshot: Path, *faults: Path, capsys: Any) -> Path:
    arguments = [
        "report",
        "--root",
        str(root),
        "build",
        "--snapshot",
        str(snapshot),
        "--config",
        str(
            Path(__file__).resolve().parents[2]
            / "analysis/configs/evaluation.toml.example"
        ),
        "--seed",
        "3",
    ]
    for one in faults:
        arguments.extend(["--faults", str(one)])
    capsys.readouterr()
    code = main(arguments)
    out = capsys.readouterr()
    assert code == 0, out.err
    return Path(out.out.splitlines()[0].split(": ", 1)[1].rsplit(" (", 1)[0])


def rows(run: Path) -> list[dict[str, Any]]:
    data = (run / "reliability.jsonl").read_bytes()
    return [json.loads(line) for line in data.splitlines()]


def of(found: list[dict[str, Any]], row: str, **match: str) -> list[dict[str, Any]]:
    return [
        one
        for one in found
        if one["row"] == row and all(one.get(k) == v for k, v in match.items())
    ]


def test_a_report_judges_the_run_again_and_times_every_fault(
    raw_snapshot: Any, archive_world: Any, datasets_root: Path, capsys: Any
) -> None:
    fault_run = published(datasets_root)
    run = build(datasets_root, raw_snapshot(archive_world), fault_run, capsys=capsys)
    found = rows(run)

    assert of(found, "fault_run")[0]["agrees_with_published"] is True
    detected = of(found, "latency", check="detected", kind="all")[0]
    assert (detected["n"], detected["median_s"], detected["within"]) == (1, 90.0, 1)
    assert of(found, "latency", check="replanned", kind="all")[0]["max_s"] == 30.0
    sc5 = of(found, "sc5")[0]
    assert sc5["status"] == "measured"
    assert sc5["simulated"] is True
    assert sc5["all_within"] is True
    assert of(found, "platform_check", kind="api_paused")
    assert (run / "fault_detection.svg").is_file()
    assert "Fault runs — SIMULATED" in (run / "report.md").read_text(encoding="utf-8")


def test_without_a_fault_run_the_figures_that_need_one_are_not_measured(
    raw_snapshot: Any, archive_world: Any, datasets_root: Path, capsys: Any
) -> None:
    found = rows(build(datasets_root, raw_snapshot(archive_world), capsys=capsys))

    assert of(found, "sc5")[0]["status"] == "not measured"
    assert of(found, "long_run")[0]["status"] == "not run"
    assert of(found, "sc4")
    assert of(found, "indicators", population="measured")


def long_run_row(run: Path) -> dict[str, Any]:
    from meridian.reports.fault_rows import fault_rows

    found = fault_rows([read_fault_run(run)], detection_max_s=90)
    return next(one for one in found if one["row"] == "long_run")


def test_the_long_run_is_included_only_when_a_run_spans_72_hours(
    raw_snapshot: Any, archive_world: Any, datasets_root: Path, capsys: Any
) -> None:
    long = published(datasets_root, hours=73.0, record=run_record(73.0))
    found = rows(build(datasets_root, raw_snapshot(archive_world), long, capsys=capsys))

    long_run = of(found, "long_run")[0]
    assert long_run["status"] == "included"
    assert long_run["hours"] >= 72
    assert long_run["record"]["environment"]["arch"] == "aarch64"
    report = (long.parent.parent / "reports").glob("*/report.md")
    text = next(report).read_text("utf-8")
    assert "72-hour run**: included" in text
    assert "on aarch64" in text


def test_a_run_shorter_than_72_hours_is_too_short(datasets_root: Path) -> None:
    row = long_run_row(published(datasets_root, 2.0, run_record(2.0)))

    assert (row["status"], row["hours"]) == ("too short", 2.0)


def test_a_run_whose_own_judgement_failed_is_not_included(datasets_root: Path) -> None:
    record = run_record(73.0, "the host slept 1 time(s)")
    row = long_run_row(published(datasets_root, 73.0, record))

    assert row["status"] == "failed"
    assert row["reasons"] == ["the host slept 1 time(s)"]


def test_a_fault_run_without_a_record_is_not_a_long_run(datasets_root: Path) -> None:
    """Positive control: 73 hours of faults alone do not make the acceptance run."""
    row = long_run_row(published(datasets_root, hours=73.0))

    assert row["status"] == "not run"


def test_a_record_that_contradicts_itself_is_refused() -> None:
    document = json.loads(json.dumps(run_record(73.0).document))
    document["passed"] = False

    with pytest.raises(LongRunRecordError, match="passed"):
        parse_long_run_record(json.dumps(document).encode())
    with pytest.raises(LongRunRecordError, match="format"):
        parse_long_run_record(b'{"format": "something-else"}')


def test_the_record_is_sealed_with_the_run(datasets_root: Path) -> None:
    run = read_fault_run(published(datasets_root, 73.0, run_record(73.0)))

    assert run.record is not None
    assert run.record.seed == 4471
    assert "long_run.json" in run.directory.files


def test_verify_finds_a_fault_run_that_moved_and_refuses_a_tampered_one(
    raw_snapshot: Any,
    archive_world: Any,
    datasets_root: Path,
    tmp_path: Path,
    capsys: Any,
) -> None:
    fault_run = published(datasets_root)
    run = build(datasets_root, raw_snapshot(archive_world), fault_run, capsys=capsys)
    moved = tmp_path / "moved"
    shutil.copytree(fault_run, moved)
    for path in [fault_run, *fault_run.rglob("*")]:
        path.chmod(0o700 if path.is_dir() else 0o600)
    shutil.rmtree(fault_run)

    assert main(["report", "--root", str(datasets_root), "verify", str(run)]) == 1
    assert "--faults" in capsys.readouterr().err
    verify = ["report", "--root", str(datasets_root), "verify", str(run)]
    assert main([*verify, "--faults", str(moved)]) == 0

    evidence = moved / "evidence.jsonl"
    for path in [moved, evidence]:
        path.chmod(0o700 if path.is_dir() else 0o600)
    evidence.write_bytes(evidence.read_bytes().replace(b"12:10:30", b"12:10:31"))
    assert main([*verify, "--faults", str(moved)]) == EXIT_CORRUPT


def test_sc5_says_why_when_the_runs_given_timed_no_detection(
    datasets_root: Path,
) -> None:
    """Found in review: it said no run was given when one was."""
    from meridian.reports.fault_rows import fault_rows

    text = ledger()
    platform_line = "\n".join(text.splitlines()[2:]) + "\n"
    platform = gathered(faults_of(text))[1:]
    path = publish_fault_run(
        platform_line,
        platform,
        [judge_gathered(one) for one in platform],
        root=datasets_root,
        stamp=("0025", T0 + timedelta(hours=2)),
    ).path

    rows = fault_rows([read_fault_run(path)], detection_max_s=90)
    sc5 = next(one for one in rows if one["row"] == "sc5")

    assert sc5["status"] == "not measured"
    assert "no station fault was timed" in str(sc5["reason"])
