"""``meridian.datasets.labels`` — D-146's rules and D-147's evidence, case by case.

One case per label, then one per place two rules could both claim a pass.
Everything is built from typed rows in memory: labelling is a pure function,
so no case needs a database, a file or a clock.

Reference: docs/DECISIONS.md D-146, D-147.
"""

from __future__ import annotations

import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from meridian.datasets.canonical import canonical_line
from meridian.datasets.label_config import LabelConfig
from meridian.datasets.labels import (
    EXCLUSIONS,
    LABELS,
    LabelledPass,
    label_counts,
    label_passes,
)
from meridian.datasets.snapshot_rows import (
    ArchiveReception,
    AssignmentRow,
    HeartbeatRow,
    ObservationRow,
    PassRow,
    SnapshotRows,
)

AS_OF = datetime(2026, 9, 23, tzinfo=UTC)
AOS = AS_OF - timedelta(days=3)
"""Well inside the default 24-hour settle margin."""

SATELLITE = "norad:57166"


def a_pass(
    pass_id: int = 1,
    *,
    station: str = "st_a",
    satellite: str = SATELLITE,
    aos: datetime = AOS,
    simulated: bool = False,
) -> PassRow:
    return PassRow(
        pass_id=pass_id,
        station_id=station,
        satellite_id=satellite,
        aos=aos,
        los=aos + timedelta(minutes=11),
        simulated=simulated,
    )


def assigned(
    target: PassRow, assignment_id: str | None = None, **overrides: Any
) -> AssignmentRow:
    """A scheduled, reported assignment over the pass's window, under config A."""
    base = AssignmentRow(
        assignment_id=assignment_id or f"as_{target.pass_id}",
        pass_id=target.pass_id,
        station_id=target.station_id,
        start_at=target.aos - timedelta(seconds=5),
        end_at=target.los + timedelta(seconds=5),
        decision="scheduled",
        state="reported",
        model_config="A",
        simulated=target.simulated,
    )
    return replace(base, **overrides)


def report(assignment_id: str, outcome: str, revision: int = 1) -> ObservationRow:
    return ObservationRow(
        assignment_id=assignment_id, revision=revision, outcome=outcome, simulated=False
    )


def heard(target: PassRow, minutes: float = 3) -> HeartbeatRow:
    return HeartbeatRow(
        station_id=target.station_id,
        received_at=target.aos + timedelta(minutes=minutes),
    )


def rows(**tables: Any) -> SnapshotRows:
    fields: dict[str, Any] = {
        "passes": (),
        "assignments": (),
        "observations": (),
        "heartbeats": (),
        "listening": {},
        "archive": (),
    }
    return SnapshotRows(**(fields | tables))


def label(snapshot: SnapshotRows, config: LabelConfig | None = None) -> LabelledPass:
    """The label of pass 1, which every case is about."""
    labelled = label_passes(snapshot, as_of=AS_OF, config=config or LabelConfig())
    return next(one for one in labelled if one.pass_id == 1)


def reported(
    outcome: str, *, confirmed: bool | None = True, **extra: Any
) -> SnapshotRows:
    """Pass 1, scheduled once, reported with ``outcome``, heard during its window.

    ``confirmed`` is the registry's answer for pass 1; ``extra`` adds rows, and
    its ``listening`` answers are merged with that one rather than replacing it.
    """
    target = a_pass()
    own = {} if confirmed is None else {"as_1": confirmed}
    tables: dict[str, Any] = {
        "passes": (target, *extra.pop("passes", ())),
        "assignments": (assigned(target), *extra.pop("assignments", ())),
        "observations": (report("as_1", outcome), *extra.pop("observations", ())),
        "heartbeats": (heard(target),),
        "listening": own | extra.pop("listening", {}),
    }
    return rows(**(tables | extra))


def other_pass(
    pass_id: int, outcome: str, *, listening: bool = True, **fields: Any
) -> dict[str, Any]:
    """A second pass of the same satellite from another station, reported."""
    other = a_pass(pass_id, station="st_b", **fields)
    return {
        "passes": (other,),
        "assignments": (assigned(other),),
        "observations": (report(f"as_{pass_id}", outcome),),
        "listening": {f"as_{pass_id}": listening},
    }


def merged(*parts: dict[str, Any]) -> dict[str, Any]:
    """Several :func:`other_pass` tables, joined into one set of extras."""
    joined: dict[str, Any] = {
        "passes": (),
        "assignments": (),
        "observations": (),
        "listening": {},
    }
    for part in parts:
        for key, value in part.items():
            joined[key] = (
                joined[key] | value if key == "listening" else joined[key] + value
            )
    return joined


def with_listening(snapshot: SnapshotRows, extra: dict[str, bool]) -> SnapshotRows:
    return replace(snapshot, listening=dict(snapshot.listening) | extra)


# --- one case per label -------------------------------------------------------


def test_a_pass_whose_report_may_still_be_queued_is_excluded() -> None:
    target = a_pass(aos=AS_OF - timedelta(hours=2))
    labelled = label(rows(passes=(target,), assignments=(assigned(target),)))

    assert labelled.label is None
    assert labelled.exclusion_reason == "report_window_open"


def test_a_pass_nothing_scheduled_is_excluded_but_kept() -> None:
    """It still counts in the completeness denominator (Stage 16)."""
    target = a_pass()
    skipped = assigned(target, decision="skipped", state="expired")

    labelled = label(rows(passes=(target,), assignments=(skipped,)))

    assert (labelled.label, labelled.exclusion_reason) == (None, "not_scheduled")
    assert labelled.scheduled_by == ()


def test_an_assignment_that_expired_unreported_was_declined() -> None:
    target = a_pass()

    labelled = label(
        rows(passes=(target,), assignments=(assigned(target, state="expired"),))
    )

    assert labelled.label == "assignment_declined"


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        ("decoded", "successful_reception"),
        ("signal_no_decode", "signal_no_decode"),
        ("aborted", "station_unavailable"),
        ("not_attempted", "station_unavailable"),
    ],
)
def test_a_report_with_an_outcome_of_its_own_says_what_happened(
    outcome: str, expected: str
) -> None:
    labelled = label(reported(outcome))

    assert labelled.label == expected
    assert labelled.source_outcome == outcome
    assert labelled.exclusion_reason is None


@pytest.mark.parametrize("outcome", ["no_signal", None])
def test_silence_with_no_heartbeat_at_all_is_a_station_that_was_not_there(
    outcome: str | None,
) -> None:
    target = a_pass()
    observations = () if outcome is None else (report("as_1", outcome),)

    labelled = label(
        rows(
            passes=(target,),
            assignments=(assigned(target, state="issued"),),
            observations=observations,
        )
    )

    assert labelled.label == "station_unavailable"


@pytest.mark.parametrize("listening", [False, None], ids=["answered-no", "not-asked"])
def test_silence_the_registry_did_not_confirm_is_not_a_miss(
    listening: bool | None,
) -> None:
    """Rule 7 of CLAUDE.md, the reason this stage exists."""
    labelled = label(reported("no_signal", confirmed=listening))

    assert labelled.label == "station_not_confirmed_listening"
    assert labelled.listening_confirmed is listening


def test_confirmed_silence_while_the_satellite_was_heard_elsewhere_is_a_miss() -> None:
    labelled = label(reported("no_signal", **other_pass(2, "decoded")))

    assert labelled.label == "confirmed_miss"
    assert labelled.exclusion_reason is None


def test_confirmed_silence_everywhere_is_a_silent_satellite() -> None:
    snapshot = reported(
        "no_signal",
        **merged(other_pass(2, "no_signal"), other_pass(3, "no_signal")),
    )

    labelled = label(snapshot)

    assert labelled.label == "satellite_silent"
    assert labelled.exclusion_reason == "satellite_silent"


def test_confirmed_silence_with_no_evidence_either_way_is_indeterminate() -> None:
    labelled = label(reported("no_signal"))

    assert labelled.label == "satellite_state_indeterminate"
    assert labelled.exclusion_reason == "satellite_state_indeterminate"


def test_a_missing_report_with_confirmed_listening_is_judged_the_same_way() -> None:
    """Absence plus confirmed listening is what rule 7 allows to be a miss."""
    target = a_pass()
    snapshot = rows(
        passes=(target, *other_pass(2, "decoded")["passes"]),
        assignments=(
            assigned(target, state="held"),
            *other_pass(2, "decoded")["assignments"],
        ),
        observations=other_pass(2, "decoded")["observations"],
        heartbeats=(heard(target),),
        listening={"as_1": True, "as_2": True},
    )

    labelled = label(snapshot)

    assert labelled.label == "confirmed_miss"
    assert labelled.source_outcome is None


# --- where two rules could both claim a pass ----------------------------------


def test_a_report_outranks_an_expired_state() -> None:
    target = a_pass()
    snapshot = rows(
        passes=(target,),
        assignments=(assigned(target, state="expired"),),
        observations=(report("as_1", "decoded"),),
    )

    assert label(snapshot).label == "successful_reception"


def test_two_configurations_pool_their_evidence_and_the_best_report_wins() -> None:
    target = a_pass()
    snapshot = rows(
        passes=(target,),
        assignments=(
            assigned(target, "as_b", model_config="B"),
            assigned(target, "as_a", model_config="A"),
        ),
        observations=(report("as_b", "no_signal"), report("as_a", "decoded")),
    )

    labelled = label(snapshot)

    assert labelled.label == "successful_reception"
    assert labelled.scheduled_by == ("A", "B")


def test_a_configuration_that_was_never_recorded_sorts_first() -> None:
    target = a_pass()
    snapshot = rows(
        passes=(target,),
        assignments=(
            assigned(target, "as_a", model_config="A"),
            assigned(target, "as_old", model_config=None),
        ),
        observations=(report("as_a", "decoded"),),
    )

    assert label(snapshot).scheduled_by == (None, "A")


def test_the_latest_revision_is_the_report() -> None:
    """A correction replaces what it corrects (D-015)."""
    snapshot = reported(
        "decoded", observations=(report("as_1", "signal_no_decode", revision=2),)
    )

    assert label(snapshot).label == "signal_no_decode"


def test_a_simulated_pass_is_labelled_and_excluded() -> None:
    """Labelled, so the simulator exercises the rules; excluded from training."""
    target = a_pass(simulated=True)
    snapshot = rows(
        passes=(target,),
        assignments=(assigned(target),),
        observations=(report("as_1", "decoded"),),
    )

    labelled = label(snapshot)

    assert labelled.label == "successful_reception"
    assert labelled.exclusion_reason == "simulated"
    assert labelled.simulated is True


def test_a_simulated_assignment_makes_the_pass_simulated() -> None:
    target = a_pass()
    snapshot = rows(passes=(target,), assignments=(assigned(target, simulated=True),))

    assert label(snapshot).simulated is True


def test_a_simulated_reception_is_not_evidence_about_a_measured_pass() -> None:
    assert (
        label(reported("no_signal", **other_pass(2, "decoded", simulated=True))).label
        == "satellite_state_indeterminate"
    )


def test_an_unconfirmed_silence_elsewhere_is_not_evidence_of_silence() -> None:
    snapshot = reported(
        "no_signal",
        **merged(
            other_pass(2, "no_signal", listening=False),
            other_pass(3, "no_signal", listening=False),
        ),
    )

    assert label(snapshot).label == "satellite_state_indeterminate"


def test_one_confirmed_silence_is_not_enough_to_call_a_satellite_silent() -> None:
    assert (
        label(reported("no_signal", **other_pass(2, "no_signal"))).label
        == "satellite_state_indeterminate"
    )


def test_the_threshold_for_silence_is_configuration() -> None:
    lenient = LabelConfig(silent_min_attempts=1)

    assert (
        label(reported("no_signal", **other_pass(2, "no_signal")), lenient).label
        == "satellite_silent"
    )


def test_evidence_outside_the_window_does_not_count() -> None:
    far = other_pass(2, "decoded", aos=AOS - timedelta(days=1))

    assert label(reported("no_signal", **far)).label == "satellite_state_indeterminate"


def test_evidence_about_another_satellite_does_not_count() -> None:
    other = other_pass(2, "decoded", satellite="norad:99999")

    assert (
        label(reported("no_signal", **other)).label == "satellite_state_indeterminate"
    )


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [("decoded", "confirmed_miss"), ("no_data", "satellite_state_indeterminate")],
)
def test_an_archive_reception_is_evidence_for_a_measured_pass(
    outcome: str, expected: str
) -> None:
    """Keyed as ingest stores it, ``norad:<number>`` — see
    test_ingest_reference_adapter.py's namespacing test — which is our own
    ``satellite_id`` form, so the two compare as stored."""
    archive = (
        ArchiveReception(
            satellite_key="norad:57166",
            satellite_key_kind="norad",
            started_at=AOS + timedelta(hours=2),
            archive_outcome=outcome,
        ),
    )

    assert label(reported("no_signal", archive=archive)).label == expected


def test_two_archive_no_data_rows_call_a_satellite_silent() -> None:
    silent = ArchiveReception(
        "norad:57166", "norad", AOS + timedelta(hours=1), "no_data"
    )

    snapshot = reported("no_signal", archive=(silent, silent))

    assert label(snapshot).label == "satellite_silent"


def test_an_archive_key_we_cannot_match_is_not_evidence() -> None:
    unmatched = ArchiveReception("NOAA 19", "source_name", AOS, "decoded")

    assert (
        label(reported("no_signal", archive=(unmatched,))).label
        == "satellite_state_indeterminate"
    )


def test_an_archive_is_never_evidence_about_a_simulated_pass() -> None:
    target = a_pass(simulated=True)
    snapshot = rows(
        passes=(target,),
        assignments=(assigned(target),),
        observations=(report("as_1", "no_signal"),),
        heartbeats=(heard(target),),
        listening={"as_1": True},
        archive=(ArchiveReception("norad:57166", "norad", AOS, "decoded"),),
    )

    assert label(snapshot).label == "satellite_state_indeterminate"


def test_a_heartbeat_outside_the_window_does_not_count() -> None:
    target = a_pass()
    snapshot = rows(
        passes=(target,),
        assignments=(assigned(target),),
        observations=(report("as_1", "no_signal"),),
        heartbeats=(heard(target, minutes=-30),),
        listening={"as_1": True},
    )

    assert label(snapshot).label == "station_unavailable"


def test_a_heartbeat_on_the_closing_instant_belongs_to_the_next_window() -> None:
    """Half-open ``[start_at, end_at)``, as ``Registry.was_listening`` reads it."""
    target = a_pass()
    window = assigned(target)
    on_the_edge = HeartbeatRow(station_id=target.station_id, received_at=window.end_at)
    snapshot = rows(
        passes=(target,),
        assignments=(window,),
        observations=(report("as_1", "no_signal"),),
        heartbeats=(on_the_edge,),
        listening={"as_1": True},
    )

    assert label(snapshot).label == "station_unavailable"


def test_a_pass_made_simulated_by_its_assignment_is_judged_as_simulated() -> None:
    """The population the row is counted under is the one its evidence comes from.

    The pass row says measured; its assignment says simulated, so the labelled
    row is simulated — and an archive, which describes the real sky, must not
    be its evidence.
    """
    target = a_pass()
    snapshot = rows(
        passes=(target,),
        assignments=(assigned(target, simulated=True),),
        observations=(report("as_1", "no_signal"),),
        heartbeats=(heard(target),),
        listening={"as_1": True},
        archive=(ArchiveReception("norad:57166", "norad", AOS, "decoded"),),
    )

    labelled = label(snapshot)

    assert labelled.simulated is True
    assert labelled.label == "satellite_state_indeterminate"


def test_the_settle_margin_is_measured_from_the_widest_window() -> None:
    """An assignment widened past ``los`` has not closed until it has."""
    target = a_pass(aos=AS_OF - timedelta(minutes=30))
    config = LabelConfig(settle_margin_s=0)
    wide = replace(assigned(target), end_at=AS_OF + timedelta(minutes=1))

    labelled = label(rows(passes=(target,), assignments=(wide,)), config)

    assert labelled.exclusion_reason == "report_window_open"


# --- the whole ---------------------------------------------------------------


def test_the_order_rows_arrive_in_does_not_change_the_labels() -> None:
    snapshot = reported(
        "no_signal",
        **merged(other_pass(2, "decoded"), other_pass(3, "no_signal")),
    )
    shuffled = replace(
        snapshot,
        passes=tuple(random.Random(7).sample(snapshot.passes, 3)),
        assignments=tuple(reversed(snapshot.assignments)),
    )

    config = LabelConfig()
    assert label_passes(shuffled, as_of=AS_OF, config=config) == label_passes(
        snapshot, as_of=AS_OF, config=config
    )


def test_every_pass_gets_exactly_one_row_in_pass_order() -> None:
    snapshot = reported("decoded", **other_pass(2, "decoded"))

    labelled = label_passes(snapshot, as_of=AS_OF, config=LabelConfig())

    assert [one.pass_id for one in labelled] == [1, 2]


def test_every_row_is_a_canonical_line() -> None:
    for one in label_passes(reported("decoded"), as_of=AS_OF, config=LabelConfig()):
        assert canonical_line(one.row()).endswith(b"\n")


def test_counts_name_every_label_and_reason_for_both_populations() -> None:
    labelled = label_passes(reported("decoded"), as_of=AS_OF, config=LabelConfig())

    counts = label_counts(labelled)

    assert counts["labels.successful_reception.measured"] == 1
    assert counts["labels.successful_reception.simulated"] == 0
    assert len(counts) == 2 * (len(LABELS) + len(EXCLUSIONS))


def test_listening_answers_for_other_assignments_do_not_leak() -> None:
    snapshot = with_listening(reported("no_signal", confirmed=None), {"as_9": True})

    assert label(snapshot).label == "station_not_confirmed_listening"
