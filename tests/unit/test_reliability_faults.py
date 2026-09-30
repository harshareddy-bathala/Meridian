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
    given_back: bool = False,
) -> StationWork:
    """One scheduled assignment of station 1.

    ``given_back`` is D-171's reinstatement: the assignment's own columns say
    held again, and only the revocation history remembers (D-196).
    """
    at = None if revoked is None else s(revoked)
    return StationWork(
        assignment_id=assignment_id,
        pass_id=1,
        decided_at=s(decided),
        start_at=s(start),
        end_at=s(start + length),
        state="held" if given_back else "revoked" if revoked_reason else "issued",
        revoked_reason=None if given_back else revoked_reason,
        revoked_at=None if given_back else at,
        redecided_at=None if redecided is None else s(redecided),
        offline_revocations=(at,) if revoked_reason == "offline" and at else (),
        declined_at=at if revoked_reason == "declined" else None,
        revocations=(at,) if at else (),
        # Given back a minute after it was taken, as a returning station would.
        reinstatements=(at + timedelta(seconds=60),) if given_back and at else (),
    )


def evidence(**changes: object) -> StationEvidence:
    """A station heard until T0, silent three minutes, then heard again."""
    base = StationEvidence(
        heartbeats=beats(-120, 0) + beats(181, 301),
        work=(),
        rounds=(),
        classifications={},
        reported=frozenset(),
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


def test_a_revocation_given_back_since_still_counts() -> None:
    """D-196: the station came back holding it, and the history still says so."""
    given_back = evidence(
        work=(work(revoked_reason="offline", revoked=120, given_back=True),),
        rounds=(s(120),),
    )

    assert check(judge_station_fault(fault(), given_back), "replanned")[0] is True


def test_a_revocation_from_an_earlier_outage_does_not_count_for_this_one() -> None:
    """Each outage is judged by the revocations inside it."""
    earlier = evidence(
        work=(work(revoked_reason="offline", revoked=-600, given_back=True),),
        rounds=(s(120),),
    )

    assert check(judge_station_fault(fault(), earlier), "replanned")[0] is False


def test_a_round_still_reading_when_the_station_returned_owes_nothing() -> None:
    """Began while offline, read after the station was back: it saw it back.

    Found by Stage 21's rehearsal: a round began a second before a heartbeat
    and wrote ten seconds after it.
    """
    returned_mid_round = evidence(
        work=(work(),), rounds=(s(180),), round_ends={s(180): s(190)}
    )

    assert (
        check(judge_station_fault(fault(), returned_mid_round), "replanned")[0] is None
    )


def test_work_already_taken_in_an_earlier_outage_is_not_owed_again() -> None:
    """Revoked before, never given back: this round had nothing left to revoke.

    Found by Stage 21's two-hour rehearsal on the real stack, where a station
    that came back did not always get its work back; the in-process gate's
    stations always did.
    """
    taken = evidence(
        work=(work(revoked_reason="offline", revoked=-600),), rounds=(s(120),)
    )

    passed, detail = check(judge_station_fault(fault(), taken), "replanned")

    assert passed is True
    assert detail == "it held no unbegun work to revoke"


def test_work_revoked_as_offline_in_a_short_gap_fails() -> None:
    """D-190: a heartbeat delay short of ninety seconds costs the station nothing."""
    short = beats(-120, 0) + beats(60, 300)
    revoked = evidence(
        heartbeats=short, work=(work(revoked_reason="offline", revoked=30),)
    )
    at_the_heartbeat = evidence(
        heartbeats=short, work=(work(revoked_reason="offline", revoked=0),)
    )
    delay = fault("heartbeat_delayed", closed=60)

    assert check(judge_station_fault(delay, revoked), "replanned")[0] is False
    assert check(judge_station_fault(delay, at_the_heartbeat), "replanned")[0] is None


def test_unbegun_work_left_to_an_offline_station_fails() -> None:
    """A round ran while it was offline and left its work in place."""
    kept = evidence(work=(work(),), rounds=(s(120),))

    assert check(judge_station_fault(fault(), kept), "replanned")[0] is False


def test_with_no_round_while_offline_replanning_is_not_asked() -> None:
    """Nothing could have revoked it; the question does not apply."""
    quiet = evidence(work=(work(),))

    assert check(judge_station_fault(fault(), quiet), "replanned")[0] is None


def test_a_reported_pass_classified_a_miss_is_a_false_miss() -> None:
    """CLAUDE.md rule 7: a pass the platform holds a report of was not missed."""
    during = work(start=30, length=120)
    missed = evidence(
        work=(during,),
        classifications={"as_1": "confirmed_miss"},
        reported=frozenset({"as_1"}),
    )

    assert check(judge_station_fault(fault(), missed), "no_false_miss")[0] is False


def test_a_pass_the_station_never_began_is_not_a_miss() -> None:
    """The ledger says the receiver was down: the station did not listen."""
    dead = replace(fault("receiver_down"), assignment_ids=("as_1",))
    missed = evidence(
        work=(work(start=30, length=120),), classifications={"as_1": "confirmed_miss"}
    )

    assert check(judge_station_fault(dead, missed), "no_false_miss")[0] is False


def test_a_pass_listened_to_and_never_reported_is_a_true_miss() -> None:
    """Heard listening, no report ever: the station missed it, and that is true."""
    lost = evidence(
        work=(work(start=30, length=120),), classifications={"as_1": "confirmed_miss"}
    )

    assert check(judge_station_fault(fault(), lost), "no_false_miss")[0] is True


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


def test_a_decline_heard_only_after_the_window_opened_is_not_owed_a_revocation() -> (
    None
):
    """MSP §4.2: absent once the window has passed is expired, not revoked."""
    late = replace(fault("declines"), assignment_ids=("as_1",), acted_at={"as_1": s(1)})
    unheard_until_it_began = evidence(
        heartbeats=beats(-120, 0) + beats(700, 900), work=(work(start=600),)
    )
    heard_in_time = evidence(work=(work(start=600),))

    assert check(
        judge_station_fault(late, unheard_until_it_began), "declines_honoured"
    )[0]
    assert (
        check(judge_station_fault(late, heard_in_time), "declines_honoured")[0] is False
    )


def test_recovery_waits_for_an_overlapping_outage_to_end() -> None:
    """A heartbeat delay ending inside a network outage is not owed a heartbeat."""
    delay = fault("heartbeat_delayed", closed=61)
    outage = replace(fault("network_down", closed=181), opened_at=s(31))
    heard_after_outage = evidence()

    assert check(
        judge_station_fault(delay, heard_after_outage, (outage,)), "recovered"
    ) == (True, "first heartbeat 0 s after it ended")
    assert (
        check(judge_station_fault(delay, heard_after_outage), "recovered")[0] is False
    )


def test_a_fault_that_leaves_the_station_reachable_is_not_asked_to_recover() -> None:
    """A decoder or a clock fault stops no heartbeat, so recovery is not a question."""
    verdict = judge_station_fault(fault("decoder_degraded"), evidence())

    assert check(verdict, "recovered")[0] is None


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
        "StationOffline fired 104 s after the fault, 15 s after it read offline",
    )
    early = evidence(alert_asked=True, alert_fired_at=s(20))
    assert check(judge_station_fault(fault(), early), "alerted")[0] is None
    assert check(judge_station_fault(fault(), silent), "alerted")[0] is False
    assert all(
        one.name != "alerted" for one in judge_station_fault(fault(), evidence()).checks
    )


@pytest.mark.parametrize(
    "kind", ["signal_degradation", "obstruction", "interference", "satellite_silent"]
)
def test_a_ground_truth_fault_asks_none_of_the_liveness_questions(kind: str) -> None:
    """Stage 25's faults change what a station measures, never whether it is heard.

    A sky run's ledger can go through this judge unchanged: its questions are
    about a station falling silent, so none that asks about the fault applies,
    and the verdict passes rather than failing a fault it was never built to
    score. Stage 27 scores these (D-253).
    """
    verdict = judge_station_fault(fault(kind, closed=None), evidence())

    assert verdict.passed
    for name in ("held", "detected", "no_false_miss", "recovered"):
        assert check(verdict, name)[0] is None


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


def test_no_reported_pass_during_a_platform_fault_may_be_a_miss() -> None:
    """A miss the platform holds a report of is a miss it made wrongly."""
    missed = PlatformEvidence(s(70), None, ("as_9",), s(3600))

    assert not judge_platform_fault(platform_fault("database_restart"), missed).passed


def test_the_platform_reads_a_ledger_the_simulator_wrote(tmp_path: Path) -> None:
    """Two readers of one format, in packages that share no code (D-189)."""
    book = FaultLedger(tmp_path / "faults.jsonl", "run-1")
    book.open("declines", "station:1", T0, tick=3, station_id="st_1", seed=7)
    book.act("declines", "station:1", s(1), ("as_1", "as_2"), tick=3)
    book.close("declines", "station:1", s(90), tick=6)
    book.open(
        "clock_drift",
        "station:2",
        s(30),
        station_id="st_2",
        detail={"drift_s_per_tick": 1.5},
    )

    with book.path.open(encoding="utf-8") as handle:
        faults = read_fault_ledger(handle)

    assert [(one.kind, one.station_id, one.closed_at) for one in faults] == [
        ("declines", "st_1", s(90)),
        ("clock_drift", "st_2", None),
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
        '{"ledger": 1, "event": "open", "run_id": "r", "kind": "k",'
        ' "target": "station:1", "at": "2026-09-29T12:00:00Z"}',
    ],
    ids=[
        "not-json",
        "other-version",
        "zoneless",
        "close-unopened",
        "unknown-event",
        "station-unnamed",
    ],
)
def test_a_ledger_line_it_cannot_read_is_refused(line: str) -> None:
    """A verdict against part of the truth is a verdict against another run."""
    with pytest.raises(FaultLedgerError, match="line 1"):
        read_fault_ledger([line])


def test_an_offline_spell_too_short_to_scrape_is_owed_no_alert() -> None:
    """Offline for 30 s: a correct platform can let it pass unseen by a rule."""
    brief = evidence(heartbeats=beats(-120, 0) + beats(120, 300), alert_asked=True)

    passed, detail = check(judge_station_fault(fault(closed=110), brief), "alerted")

    assert passed is None
    assert "too briefly" in detail


def test_an_alert_already_firing_times_nothing_about_this_fault() -> None:
    """StationOffline is summed over the fleet: another station's alert is theirs."""
    inherited = evidence(alert_asked=True, alert_already_firing=True)

    passed, detail = check(judge_station_fault(fault(), inherited), "alerted")

    assert passed is None
    assert "not attributable" in detail


def test_a_rise_is_the_first_firing_sample_after_the_fault() -> None:
    """Already firing at the fault's start is no rise; a later sample is."""
    from meridian.reliability.fault_check import first_rise

    start = T0.timestamp()

    assert first_rise([start - 5, start + 60], start).already_firing
    rose = first_rise([start - 600, start + 70, start + 75], start)
    assert (rose.fired_at, rose.already_firing) == (s(70), False)
    assert first_rise([], start).fired_at is None
    assert first_rise([start + 70], start, end_s=start + 60).fired_at is None
