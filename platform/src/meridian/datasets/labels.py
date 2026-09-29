"""What each geometrically available pass is labelled — D-146, as a pure function.

The unit is a physical pass: every prediction of one rise over one station,
grouped by :mod:`meridian.datasets.physical_passes` (D-148), and the
denominator of Stage 16's completeness ratio. It is excluded if either of
these holds:

1. its window closed less than ``settle_margin_s`` ago → excluded,
   ``report_window_open`` — a report may still be in a station's queue;
2. nothing scheduled it → excluded, ``not_scheduled``.

Otherwise its label is :func:`meridian.reliability.classification.classify`'s,
the same classification the reliability layer counts misses by (D-180). That
module states the rules; this one gathers their evidence from a snapshot. A
revoked assignment is never the station's work, so never a miss (D-171). A
heartbeat is looked for before a decline is read (D-181), which is what
``labels-3`` changed.

**Several assignments can share a pass** — configurations A and B are
scheduled over one horizon to be compared, and a later run may schedule a
newer prediction of the same rise — and the reception is physical, so their
evidence is pooled: the most informative latest report wins, and listening
counts as confirmed if the registry confirmed it for any of them.

**No clock, no database, no file.** ``as_of`` comes from the snapshot, and the
listening answers were frozen by the registry at export (D-145). The same rows
and the same configuration always give the same labels, which is Stage 15's
gate.

Reference: docs/DECISIONS.md D-145, D-146, D-147, D-148, D-171, D-180, D-181.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from meridian.datasets.evidence import EvidenceIndex, OwnReception, satellite_state
from meridian.datasets.label_config import LabelConfig
from meridian.datasets.physical_passes import PhysicalPass, group_physical_passes
from meridian.datasets.pooled_evidence import (
    OUTCOME_ORDER,
    PooledEvidence,
    pool_evidence,
)
from meridian.datasets.snapshot_rows import AssignmentRow, PassRow, SnapshotRows
from meridian.reliability.classification import PASS_CLASSES, PassEvidence, classify

__all__ = [
    "EXCLUSIONS",
    "LABELS",
    "OUTCOME_ORDER",
    "TRANSFORMATION_VERSION",
    "LabelledPass",
    "label_counts",
    "label_passes",
]

TRANSFORMATION_VERSION = "labels-3"
"""Bumped whenever a rule here or in the classification changes, so two
datasets made under different rules can never share a hash (D-144).
``labels-2`` labelled physical passes rather than predictions (D-148);
``labels-3`` looks for a heartbeat before reading a decline (D-181)."""

LABELS: tuple[str, ...] = PASS_CLASSES

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


@dataclass(frozen=True, slots=True)
class LabelledPass:
    """One row of ``labels.jsonl``: one physical pass."""

    pass_id: int
    """The representative prediction's id (D-148)."""

    pass_ids: tuple[int, ...]
    """Every prediction of this rise, sorted."""

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
            "pass_ids": list(self.pass_ids),
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


def label_passes(
    rows: SnapshotRows,
    *,
    as_of: datetime,
    config: LabelConfig,
    since: datetime | None = None,
) -> tuple[LabelledPass, ...]:
    """Label every pass in a raw snapshot.

    Args:
        rows: The snapshot's rows.
        as_of: The snapshot's own instant, from its manifest.
        config: The labelling configuration.
        since: The snapshot's start, from its manifest. A rise that begins
            before it is not labelled: the export reads predictions of it from
            either side of ``since`` only so it can be seen whole (D-148).

    Returns:
        One labelled row per physical pass, in representative pass-id order.
    """
    physical = tuple(
        one
        for one in group_physical_passes(rows.passes)
        if since is None or one.first_aos >= since
    )
    evidence = pool_evidence(physical, rows)
    context = _Context(
        heard=_heartbeat_times(rows),
        index=EvidenceIndex.build(_own_receptions(physical, evidence), rows.archive),
        settled_by=as_of - timedelta(seconds=config.settle_margin_s),
        config=config,
    )
    return tuple(
        _label(one, evidence[one.representative.pass_id], context) for one in physical
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


def _label(
    physical: PhysicalPass, evidence: PooledEvidence, context: _Context
) -> LabelledPass:
    """Rules 1 and 2 exclude the pass; otherwise the classification labels it."""
    target = physical.representative
    chosen = (*evidence.scheduled, *evidence.revoked)
    ends = [physical.last_los, *(one.end_at for one in chosen)]
    if max(ends) > context.settled_by:
        return _row(physical, evidence, None, "report_window_open")
    if not chosen:
        return _row(physical, evidence, None, "not_scheduled")
    label = _outcome_label(target, evidence, context)
    return _row(physical, evidence, label, _exclusion(label, evidence.simulated))


def _outcome_label(target: PassRow, evidence: PooledEvidence, context: _Context) -> str:
    """The classification of a settled, scheduled pass, from the snapshot's rows."""
    return classify(
        PassEvidence(
            outcome=None if evidence.report is None else evidence.report.outcome,
            assignment_states=tuple(one.state for one in evidence.scheduled),
            revoked_reasons=tuple(one.revoked_reason or "" for one in evidence.revoked),
            heard_during_window=_heard_during(
                target.station_id, evidence.scheduled, context.heard
            ),
            listening_confirmed=evidence.listening,
        ),
        lambda: satellite_state(
            target,
            context.index,
            simulated=evidence.simulated,
            window_s=context.config.silent_window_s,
            min_silent_attempts=context.config.silent_min_attempts,
        ),
    )


def _exclusion(label: str, simulated: bool) -> str | None:
    """Why a labelled row is kept out of training, if it is."""
    if simulated:
        return "simulated"
    if label in ("satellite_silent", "satellite_state_indeterminate"):
        return label
    return None


def _row(
    physical: PhysicalPass,
    evidence: PooledEvidence,
    label: str | None,
    excluded: str | None,
) -> LabelledPass:
    target = physical.representative
    configs = {one.model_config for one in (*evidence.scheduled, *evidence.revoked)}
    return LabelledPass(
        pass_id=target.pass_id,
        pass_ids=physical.pass_ids,
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


def _own_receptions(
    physical: Iterable[PhysicalPass], evidence: Mapping[int, PooledEvidence]
) -> list[OwnReception]:
    """Every pass that was reported on, as evidence about its satellite."""
    return [
        OwnReception(
            pass_id=one.representative.pass_id,
            satellite_id=one.representative.satellite_id,
            at=one.representative,
            outcome=pooled.report.outcome,
            listening_confirmed=bool(pooled.listening),
            simulated=pooled.simulated,
        )
        for one in physical
        if (pooled := evidence[one.representative.pass_id]).report is not None
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
