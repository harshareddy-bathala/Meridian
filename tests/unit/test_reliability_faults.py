"""``meridian.reliability.faults`` — a fault judged from the platform's own records.

No marker: every verdict is a function of a ledger window and stored instants,
so each check is pinned here on stated times, with a failing case beside every
passing one. A check that can only pass proves nothing about the platform.

Reference: docs/DECISIONS.md D-189, D-190, D-192.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from meridian.reliability.faults import (
    FaultLedgerError,
    InjectedFault,
    PlatformEvidence,
    StationEvidence,
    StationWork,
    judge_platform_fault,
    judge_station_fault,
    read_fault_ledger,
)
from meridian_sim.ledger import FaultLedger

T0 = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
CADENCE = timedelta(seconds=30)


def s(seconds: float) -> datetime:
    """An instant ``seconds`` after T0."""
    return T0 + timedelta(seconds=seconds)


def beats(first: float, last: float) -> tuple[datetime, ...]:
    """A heartbeat every thirty seconds from ``first`` to ``last``, both included."""
    count = int((last - first) // 30) + 1
    return tuple(s(first + 30 * one) for one in range(count))


def fault(kind: str = "network_down", *, closed: float | None = 181) -> InjectedFault:
    """A fault on station 1 opened one second after its last heartbeat."""
    return InjectedFault(
        run_id="run-1",
        kind=kind,
        target="station:1",
        opened_at=s(1),
        closed_at=None if closed is None else s(closed),
        station_id="st_1",
    )


def work(  # noqa: PLR0913 — one stored assignment's fields
    assignment_id: str = "as_1",
    *,
    decided: float = -60,
    start: float = 600,
    length: float = 660,
    revoked_reason: str | None = None,
    revoked: float | None = None,
    redecided: float | None = None,
) -> StationWork:
    """One scheduled assignment of station 1."""
    return StationWork(
        assignment_id=assignment_id,
        pass_id=1,
        decided_at=s(decided),
        start_at=s(start),
        end_at=s(start + length),
        state="revoked" if revoked_reason else "issued",
        revoked_reason=revoked_reason,
        revoked_at=None if revoked is None else s(revoked),
        redecided_at=None if redecided is None else s(redecided),
    )


def evidence(**changes: object) -> StationEvidence:
    """A station heard until T0, silent three minutes, then heard again."""
    base = StationEvidence(
        heartbeats=beats(-120, 0) + beats(181, 301),
        work=(),
        rounds=(),
        classifications={},
        as_of=s(3600),
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def check(verdict: object, name: str) -> tuple[bool | None, str]:
    """One named check's answer and detail."""
    (found,) = [one for one in verdict.checks if one.name == name]  # type: ignore[attr-defined]
    return found.passed, found.detail


def test_a_silence_past_ninety_seconds_is_detected_within_sc_5() -> None:
    """Offline at the last heartbeat plus ninety: 89 s after the fault began."""
    passed, detail = check(judge_station_fault(fault(), evidence()), "detected")

    assert passed is True
    assert "offline 89 s after" in detail


def test_a_short_silence_is_correctly_never_offline() -> None:
    """D-190: a one-tick heartbeat delay is stale at most, and that is right."""
    short = evidence(heartbeats=beats(-120, 0) + beats(60, 300))
    verdict = judge_station_fault(fault("heartbeat_delayed", closed=60), short)

    assert check(verdict, "detected")[0] is True
    assert "never offline" in check(verdict, "detected")[1]
    assert check(verdict, "no_new_work")[0] is None
    assert check(verdict, "replanned")[0] is None


def test_a_heartbeat_stored_inside_the_window_means_the_fault_did_not_hold() -> None:
    """The ledger claims a silence the platform never saw: a broken injection."""
    leaky = evidence(heartbeats=beats(-120, 301))

    assert check(judge_station_fault(fault(), leaky), "held")[0] is False
    assert check(judge_station_fault(fault(), evidence()), "held")[0] is True


def test_a_restart_may_heartbeat_inside_its_window() -> None:
    """An instant: the process that died on one tick is back on the next."""
    verdict = judge_station_fault(fault("restart", closed=31), evidence())

    assert check(verdict, "held")[0] is None


def test_work_decided_while_offline_fails() -> None:
    """D-166: an offline station is given nothing new."""
    given = evidence(work=(work(decided=100),))

    assert check(judge_station_fault(fault(), given), "no_new_work")[0] is False
    assert check(judge_station_fault(fault(), evidence()), "no_new_work")[0] is True


def test_unbegun_work_revoked_by_a_round_while_offline_is_replanned() -> None:
    """D-171: revoked as offline, and its pass decided again once back."""
    revoked = evidence(
        work=(work(revoked_reason="offline", revoked=120, redecided=200),),
        rounds=(s(120),),
    )
    passed, detail = check(judge_station_fault(fault(), revoked), "replanned")

    assert passed is True
    assert "1 revoked 30 s after going offline; 1 decided again" in detail


def test_unbegun_work_left_to_an_offline_station_fails() -> None:
    """A round ran while it was offline and left its work in place."""
    kept = evidence(work=(work(),), rounds=(s(120),))

    assert check(judge_station_fault(fault(), kept), "replanned")[0] is False


def test_with_no_round_while_offline_replanning_is_not_asked() -> None:
    """Nothing could have revoked it; the question does not apply."""
    quiet = evidence(work=(work(),))

    assert check(judge_station_fault(fault(), quiet), "replanned")[0] is None


def test_a_pass_lost_to_a_fault_must_not_be_a_confirmed_miss() -> None:
    """CLAUDE.md rule 7, at the moment it matters most."""
    during = work(start=30, length=120)
    missed = evidence(work=(during,), classifications={"as_1": "confirmed_miss"})
    unavailable = evidence(
        work=(during,), classifications={"as_1": "station_unavailable"}
    )

    assert check(judge_station_fault(fault(), missed), "no_false_miss")[0] is False
    assert check(judge_station_fault(fault(), unavailable), "no_false_miss")[0] is True


def test_a_decoder_fault_does_not_ask_about_misses() -> None:
    """The station listened; what its hearing is classified as is Stage 27's."""
    verdict = judge_station_fault(fault("decoder_degraded"), evidence())

    assert check(verdict, "no_false_miss")[0] is None


def test_declined_work_must_be_revoked_as_declined() -> None:
    """Each assignment the ledger says was let go of, revoked for that reason."""
    declining = replace(fault("declines"), assignment_ids=("as_1",))
    honoured = evidence(work=(work(revoked_reason="declined", revoked=10),))
    ignored = evidence(work=(work(),))

    assert check(judge_station_fault(declining, honoured), "declines_honoured")[0]
    assert (
        check(judge_station_fault(declining, ignored), "declines_honoured")[0] is False
    )


def test_a_station_must_be_heard_again_after_its_fault() -> None:
    """Within ninety seconds of the close, or it is still down by SC-5's measure."""
    gone = evidence(heartbeats=beats(-120, 0))

    assert check(judge_station_fault(fault(), evidence()), "recovered")[0] is True
    assert check(judge_station_fault(fault(), gone), "recovered")[0] is False


def test_recovery_too_recent_to_judge_is_not_a_failure() -> None:
    """Read ten seconds after the close, silence says nothing yet."""
    early = evidence(heartbeats=beats(-120, 0), as_of=s(191))

    assert check(judge_station_fault(fault(), early), "recovered")[0] is None


def test_a_revoked_token_never_closes_so_recovery_is_not_asked() -> None:
    """D-024: it stops for good by design."""
    verdict = judge_station_fault(fault("token_revoked", closed=None), evidence())

    assert check(verdict, "recovered")[0] is None


def test_the_alert_is_the_measured_half_of_sc_5() -> None:
    """Asked only when given a Prometheus; owed only when the station went offline."""
    fired = evidence(alert_asked=True, alert_fired_at=s(105))
    silent = evidence(alert_asked=True)

    assert check(judge_station_fault(fault(), fired), "alerted") == (
        True,
        "StationOffline fired 104 s after the fault",
    )
    assert check(judge_station_fault(fault(), silent), "alerted")[0] is False
    assert all(
        one.name != "alerted" for one in judge_station_fault(fault(), evidence()).checks
    )


def test_a_verdict_passes_when_nothing_failed() -> None:
    """A check that did not apply is not a failure."""
    assert judge_station_fault(fault(), evidence()).passed
    assert not judge_station_fault(fault(), evidence(heartbeats=beats(-120, 0))).passed


def platform_fault(kind: str) -> InjectedFault:
    """A platform fault from T0 to a minute later."""
    return InjectedFault(
        run_id="run-1",
        kind=kind,
        target="platform:api",
        opened_at=T0,
        closed_at=s(60),
    )


def test_a_restarted_platform_must_hear_a_station_again() -> None:
    """Any station will do: the fault was the platform's, not theirs."""
    heard = PlatformEvidence(s(70), None, (), s(3600))
    unheard = PlatformEvidence(None, None, (), s(3600))

    assert judge_platform_fault(platform_fault("platform_restart"), heard).passed
    assert not judge_platform_fault(platform_fault("platform_restart"), unheard).passed


def test_a_stopped_scheduler_must_run_a_round_again() -> None:
    """Within ScheduledTaskStalled's fifteen minutes of starting again."""
    prompt = PlatformEvidence(None, s(300), (), s(3600))
    stalled = PlatformEvidence(s(70), s(2000), (), s(3600))

    assert judge_platform_fault(platform_fault("scheduler_down"), prompt).passed
    assert not judge_platform_fault(platform_fault("scheduler_down"), stalled).passed


def test_no_pass_during_a_platform_fault_may_be_a_confirmed_miss() -> None:
    """Stations could not be heard, so none can have been confirmed listening."""
    missed = PlatformEvidence(s(70), None, ("as_9",), s(3600))

    assert not judge_platform_fault(platform_fault("database_restart"), missed).passed


def test_the_platform_reads_a_ledger_the_simulator_wrote(tmp_path: Path) -> None:
    """Two readers of one format, in packages that share no code (D-189)."""
    book = FaultLedger(tmp_path / "faults.jsonl", "run-1")
    book.open("declines", "station:1", T0, tick=3, station_id="st_1", seed=7)
    book.act("declines", "station:1", s(1), ("as_1", "as_2"), tick=3)
    book.close("declines", "station:1", s(90), tick=6)
    book.open("clock_drift", "station:2", s(30), detail={"drift_s_per_tick": 1.5})

    with book.path.open(encoding="utf-8") as handle:
        faults = read_fault_ledger(handle)

    assert [(one.kind, one.station_id, one.closed_at) for one in faults] == [
        ("declines", "st_1", s(90)),
        ("clock_drift", None, None),
    ]
    assert faults[0].assignment_ids == ("as_1", "as_2")
    assert faults[1].detail == {"drift_s_per_tick": 1.5}


@pytest.mark.parametrize(
    "line",
    [
        "{",
        '{"ledger": 2}',
        '{"ledger": 1, "event": "open", "run_id": "r", "kind": "k", "target": "t",'
        ' "at": "2026-09-29T12:00:00"}',
        '{"ledger": 1, "event": "close", "run_id": "r", "kind": "k", "target": "t",'
        ' "at": "2026-09-29T12:00:00Z"}',
        '{"ledger": 1, "event": "pause", "run_id": "r", "kind": "k", "target": "t",'
        ' "at": "2026-09-29T12:00:00Z"}',
    ],
    ids=["not-json", "other-version", "zoneless", "close-unopened", "unknown-event"],
)
def test_a_ledger_line_it_cannot_read_is_refused(line: str) -> None:
    """A verdict against part of the truth is a verdict against another run."""
    with pytest.raises(FaultLedgerError, match="line 1"):
        read_fault_ledger([line])
