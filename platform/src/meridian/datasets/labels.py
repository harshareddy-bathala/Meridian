"""What each geometrically available pass is labelled — D-146, as a pure function.

The unit is a pass: one ``passes`` row, already per station and satellite, and
the denominator of Stage 16's completeness ratio. Its label is decided by the
first of these that holds:

1. its window closed less than ``settle_margin_s`` ago → excluded,
   ``report_window_open`` — a report may still be in a station's queue;
2. nothing scheduled it → excluded, ``not_scheduled``;
3. every scheduled assignment expired unreported → ``assignment_declined``;
4. the report is ``decoded`` → ``successful_reception``;
5. the report is ``signal_no_decode`` → ``signal_no_decode``;
6. the report is ``aborted`` or ``not_attempted`` → ``station_unavailable``;
7. no signal or no report, and no heartbeat at all in the window →
   ``station_unavailable``;
8. no signal or no report, and listening not confirmed →
   ``station_not_confirmed_listening``;
9. no signal or no report, listening confirmed → ``confirmed_miss``,
   ``satellite_silent`` or ``satellite_state_indeterminate``, by D-147.

**Several assignments can share a pass** — configurations A and B are
scheduled over one horizon to be compared — and the reception is physical, so
their evidence is pooled: the most informative latest report wins, and
listening counts as confirmed if the registry confirmed it for any of them.

**No clock, no database, no file.** ``as_of`` comes from the snapshot, and the
listening answers were frozen by the registry at export (D-145). The same rows
and the same configuration always give the same labels, which is Stage 15's
gate.

Reference: docs/DECISIONS.md D-145, D-146, D-147.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from meridian.datasets.evidence import EvidenceIndex, OwnReception, satellite_state
from meridian.datasets.label_config import LabelConfig
from meridian.datasets.snapshot_rows import (
    AssignmentRow,
    ObservationRow,
    PassRow,
    SnapshotRows,
)

__all__ = [
    "EXCLUSIONS",
    "LABELS",
    "OUTCOME_ORDER",
    "TRANSFORMATION_VERSION",
    "LabelledPass",
    "label_counts",
    "label_passes",
]

TRANSFORMATION_VERSION = "labels-1"
"""Bumped whenever a rule here changes, so two datasets made under different
rules can never share a hash (D-144)."""

LABELS = (
    "successful_reception",
    "signal_no_decode",
    "confirmed_miss",
    "satellite_silent",
    "satellite_state_indeterminate",
    "station_unavailable",
    "station_not_confirmed_listening",
    "assignment_declined",
)

EXCLUSIONS = (
    "report_window_open",
    "not_scheduled",
    "simulated",
    "satellite_silent",
    "satellite_state_indeterminate",
)
"""Why a row is kept out of training and yield scoring, first reason first.
The two satellite labels are excluded from yield scoring and counted apart
(``EVALUATION.md`` §5); a simulated row is excluded whatever its label (D-078)."""

OUTCOME_ORDER = ("decoded", "signal_no_decode", "no_signal", "aborted", "not_attempted")
"""Most informative first: where two assignments of one pass both reported."""

_UNAVAILABLE = frozenset(("aborted", "not_attempted"))
_SATELLITE_LABELS = {
    "transmitting": "confirmed_miss",
    "silent": "satellite_silent",
    "indeterminate": "satellite_state_indeterminate",
}


@dataclass(frozen=True, slots=True)
class LabelledPass:
    """One row of ``labels.jsonl``."""

    pass_id: int
    station_id: str
    satellite_id: str
    aos: datetime
    los: datetime
    label: str | None
    exclusion_reason: str | None
    source_outcome: str | None
    """The report's own outcome, verbatim, so every label can be audited."""
    listening_confirmed: bool | None
    """None where no scheduled assignment had closed by export."""
    scheduled_by: tuple[str | None, ...]
    simulated: bool

    def row(self) -> dict[str, object]:
        """The row as it is written."""
        return {
            "pass_id": self.pass_id,
            "station_id": self.station_id,
            "satellite_id": self.satellite_id,
            "aos": self.aos,
            "los": self.los,
            "label": self.label,
            "exclusion_reason": self.exclusion_reason,
            "source_outcome": self.source_outcome,
            "listening_confirmed": self.listening_confirmed,
            "scheduled_by": list(self.scheduled_by),
            "simulated": self.simulated,
        }


@dataclass(frozen=True, slots=True)
class _Context:
    """What every pass is judged against: the same for all of them."""

    heard: Mapping[str, list[datetime]]
    index: EvidenceIndex
    settled_by: datetime
    config: LabelConfig


@dataclass(frozen=True, slots=True)
class _Evidence:
    """What one pass's scheduled assignments add up to."""

    scheduled: tuple[AssignmentRow, ...]
    report: ObservationRow | None
    listening: bool | None
    simulated: bool


def label_passes(
    rows: SnapshotRows, *, as_of: datetime, config: LabelConfig
) -> tuple[LabelledPass, ...]:
    """Label every pass in a raw snapshot.

    Args:
        rows: The snapshot's rows.
        as_of: The snapshot's own instant, from its manifest.
        config: The labelling configuration.

    Returns:
        One labelled row per pass, in pass-id order.
    """
    by_pass = _group(rows.assignments)
    latest = _latest_reports(rows.observations)
    evidence = {
        one.pass_id: _pool(one, by_pass.get(one.pass_id, ()), latest, rows.listening)
        for one in rows.passes
    }
    context = _Context(
        heard=_heartbeat_times(rows),
        index=EvidenceIndex.build(_own_receptions(rows.passes, evidence), rows.archive),
        settled_by=as_of - timedelta(seconds=config.settle_margin_s),
        config=config,
    )
    return tuple(
        _label(one, evidence[one.pass_id], context)
        for one in sorted(rows.passes, key=lambda one: one.pass_id)
    )


def label_counts(labelled: Iterable[LabelledPass]) -> dict[str, int]:
    """Every label and exclusion, counted for measured and simulated apart.

    Every name is present, zeros included, so two manifests can be compared
    field by field and a label that never occurred says so.
    """
    counts = {
        f"{kind}.{name}.{population}": 0
        for kind, names in (("labels", LABELS), ("excluded", EXCLUSIONS))
        for name in names
        for population in ("measured", "simulated")
    }
    for one in labelled:
        population = "simulated" if one.simulated else "measured"
        if one.label is not None:
            counts[f"labels.{one.label}.{population}"] += 1
        if one.exclusion_reason is not None:
            counts[f"excluded.{one.exclusion_reason}.{population}"] += 1
    return counts


def _label(target: PassRow, evidence: _Evidence, context: _Context) -> LabelledPass:
    """Rules 1 and 2 exclude the pass; otherwise rules 3 to 9 label it."""
    ends = [target.los, *(one.end_at for one in evidence.scheduled)]
    if max(ends) > context.settled_by:
        return _row(target, evidence, None, "report_window_open")
    if not evidence.scheduled:
        return _row(target, evidence, None, "not_scheduled")
    label = _outcome_label(target, evidence, context)
    return _row(target, evidence, label, _exclusion(label, evidence.simulated))


def _outcome_label(target: PassRow, evidence: _Evidence, context: _Context) -> str:
    """Rules 3 to 9: what the report, the heartbeats and the registry say.

    Rules 3 to 8 are a first-match table, read top to bottom as D-146 writes
    them; each condition is evaluated only if every one above it failed.
    """
    outcome = None if evidence.report is None else evidence.report.outcome
    rules: tuple[tuple[Callable[[], bool], str], ...] = (
        (
            lambda: (
                outcome is None
                and all(one.state == "expired" for one in evidence.scheduled)
            ),
            "assignment_declined",
        ),
        (lambda: outcome == "decoded", "successful_reception"),
        (lambda: outcome == "signal_no_decode", "signal_no_decode"),
        (lambda: outcome in _UNAVAILABLE, "station_unavailable"),
        (
            lambda: (
                not _heard_during(target.station_id, evidence.scheduled, context.heard)
            ),
            "station_unavailable",
        ),
        (lambda: not evidence.listening, "station_not_confirmed_listening"),
    )
    for holds, label in rules:
        if holds():
            return label
    state = satellite_state(
        target,
        context.index,
        simulated=evidence.simulated,
        window_s=context.config.silent_window_s,
        min_silent_attempts=context.config.silent_min_attempts,
    )
    return _SATELLITE_LABELS[state]


def _exclusion(label: str, simulated: bool) -> str | None:
    """Why a labelled row is kept out of training, if it is."""
    if simulated:
        return "simulated"
    if label in ("satellite_silent", "satellite_state_indeterminate"):
        return label
    return None


def _row(
    target: PassRow, evidence: _Evidence, label: str | None, excluded: str | None
) -> LabelledPass:
    configs = {one.model_config for one in evidence.scheduled}
    return LabelledPass(
        pass_id=target.pass_id,
        station_id=target.station_id,
        satellite_id=target.satellite_id,
        aos=target.aos,
        los=target.los,
        label=label,
        exclusion_reason=excluded,
        source_outcome=None if evidence.report is None else evidence.report.outcome,
        listening_confirmed=evidence.listening,
        scheduled_by=tuple(sorted(configs, key=lambda one: (one is not None, one))),
        simulated=evidence.simulated,
    )


def _pool(
    target: PassRow,
    assignments: tuple[AssignmentRow, ...],
    latest: Mapping[str, ObservationRow],
    listening: Mapping[str, bool],
) -> _Evidence:
    """Pool one pass's scheduled assignments into one body of evidence."""
    scheduled = tuple(one for one in assignments if one.decision == "scheduled")
    reports = [
        latest[one.assignment_id] for one in scheduled if one.assignment_id in latest
    ]
    report = min(reports, key=_informativeness, default=None)
    answers = [
        listening[one.assignment_id]
        for one in scheduled
        if one.assignment_id in listening
    ]
    return _Evidence(
        scheduled=scheduled,
        report=report,
        listening=any(answers) if answers else None,
        simulated=target.simulated
        or any(one.simulated for one in scheduled)
        or any(one.simulated for one in reports),
    )


def _informativeness(report: ObservationRow) -> tuple[int, str]:
    """Rank a report by :data:`OUTCOME_ORDER`, then by id, so ties are stable."""
    rank = (
        OUTCOME_ORDER.index(report.outcome)
        if report.outcome in OUTCOME_ORDER
        else len(OUTCOME_ORDER)
    )
    return rank, report.assignment_id


def _latest_reports(
    observations: Iterable[ObservationRow],
) -> dict[str, ObservationRow]:
    """Each assignment's latest revision. The export kept only those by ``as_of``."""
    latest: dict[str, ObservationRow] = {}
    for one in observations:
        held = latest.get(one.assignment_id)
        if held is None or one.revision > held.revision:
            latest[one.assignment_id] = one
    return latest


def _own_receptions(
    passes: Iterable[PassRow], evidence: Mapping[int, _Evidence]
) -> list[OwnReception]:
    """Every pass that was reported on, as evidence about its satellite."""
    return [
        OwnReception(
            pass_id=one.pass_id,
            satellite_id=one.satellite_id,
            at=one,
            outcome=pooled.report.outcome,
            listening_confirmed=bool(pooled.listening),
            simulated=pooled.simulated,
        )
        for one in passes
        if (pooled := evidence[one.pass_id]).report is not None
    ]


def _heartbeat_times(rows: SnapshotRows) -> dict[str, list[datetime]]:
    """Each station's heartbeat times, sorted, for a range lookup per window."""
    times: dict[str, list[datetime]] = {}
    for one in rows.heartbeats:
        times.setdefault(one.station_id, []).append(one.received_at)
    return {station: sorted(held) for station, held in times.items()}


def _heard_during(
    station_id: str,
    scheduled: tuple[AssignmentRow, ...],
    heard: Mapping[str, list[datetime]],
) -> bool:
    """Whether any heartbeat from the station arrived inside any scheduled window.

    Windows are half-open, ``[start_at, end_at)``, as ``Registry.was_listening``
    reads them, so a heartbeat on the boundary of two back-to-back windows
    belongs to the later one only.
    """
    times = heard.get(station_id, [])
    for one in scheduled:
        first = bisect_left(times, one.start_at)
        if first < len(times) and times[first] < one.end_at:
            return True
    return False


def _group(
    assignments: Iterable[AssignmentRow],
) -> dict[int, tuple[AssignmentRow, ...]]:
    """Assignments by pass, each group in assignment-id order."""
    grouped: dict[int, list[AssignmentRow]] = {}
    for one in sorted(assignments, key=lambda one: one.assignment_id):
        grouped.setdefault(one.pass_id, []).append(one)
    return {pass_id: tuple(held) for pass_id, held in grouped.items()}
