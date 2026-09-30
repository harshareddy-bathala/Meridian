"""A fault run's evidence and verdicts as rows, and back — so it can be judged again.

``meridian reliability faults`` reads the platform's records around every fault
a ledger names and judges them (D-192). Those records keep changing: a heartbeat
aggregate refreshes, an assignment is decided again, a later run's faults land
beside them. So a verdict read today cannot be regenerated next month by reading
again. ``--publish`` therefore keeps what was read — each fault's
:class:`~meridian.reliability.fault_model.Gathered` evidence — as rows, and
:func:`gathered_from_rows` gives it back exactly, so the pure judges can reach
the same verdicts from a sealed directory with no database (D-240).

Standard library only: this package may not import ``meridian.datasets``
(``tests/unit/test_reliability_boundaries.py``), so instants are written as
ISO-8601 text here, and the command that publishes the directory seals it.

Reference: docs/DECISIONS.md D-189, D-192, D-240.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime

from meridian.reliability.fault_ledger import InjectedFault
from meridian.reliability.fault_model import (
    FaultVerdict,
    Gathered,
    PlatformEvidence,
    StationEvidence,
    StationWork,
)

__all__ = [
    "FaultRecordError",
    "evidence_rows",
    "gathered_from_rows",
    "verdict_rows",
]

Row = Mapping[str, object]

_WORK_INSTANTS = ("decided_at", "start_at", "end_at")
_WORK_OPTIONAL = ("revoked_at", "redecided_at", "declined_at")
_WORK_SERIES = ("offline_revocations", "revocations", "reinstatements")


class FaultRecordError(ValueError):
    """A row that is not the shape :func:`evidence_rows` writes."""


def evidence_rows(gathered: Sequence[Gathered]) -> list[dict[str, object]]:
    """One row per fault, in ledger order, naming its neighbours by position."""
    position = {id(one.fault): index for index, one in enumerate(gathered)}
    return [
        {"index": index}
        | (
            _platform(one.evidence)
            if isinstance(one.evidence, PlatformEvidence)
            else _station(one.evidence)
        )
        | {"alongside": [position[id(other)] for other in one.alongside]}
        for index, one in enumerate(gathered)
    ]


def verdict_rows(verdicts: Sequence[FaultVerdict]) -> list[dict[str, object]]:
    """One row per verdict, every check with its answer and its latency."""
    return [
        {
            "index": index,
            "run_id": one.fault.run_id,
            "kind": one.fault.kind,
            "target": one.fault.target,
            "station_id": one.fault.station_id,
            # A station fault is against simulated work; a platform fault was
            # done to the platform itself, which is not simulated (rule 5).
            "simulated": not one.fault.on_platform,
            "opened_at": _text(one.fault.opened_at),
            "closed_at": _optional_text(one.fault.closed_at),
            "passed": one.passed,
            "checks": [
                {
                    "name": check.name,
                    "passed": check.passed,
                    "detail": check.detail,
                    "latency_s": check.latency_s,
                }
                for check in one.checks
            ],
        }
        for index, one in enumerate(verdicts)
    ]


def gathered_from_rows(
    faults: Sequence[InjectedFault], rows: Sequence[Row]
) -> tuple[Gathered, ...]:
    """The evidence :func:`evidence_rows` wrote, beside the ledger's faults.

    Raises:
        FaultRecordError: A row is missing a field, names a fault the ledger
            does not hold, or the rows and the ledger disagree in number.
    """
    if len(rows) != len(faults):
        message = f"{len(rows)} evidence rows for a ledger of {len(faults)} faults"
        raise FaultRecordError(message)
    try:
        return tuple(
            Gathered(
                fault=faults[_index(row)],
                evidence=_evidence(row),
                alongside=tuple(faults[int(str(n))] for n in _list(row, "alongside")),
            )
            for row in rows
        )
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        message = f"an evidence row is not one this module wrote: {exc}"
        raise FaultRecordError(message) from exc


def _station(evidence: StationEvidence) -> dict[str, object]:
    return {
        "kind": "station",
        "heartbeats": [_text(one) for one in evidence.heartbeats],
        "work": [_work(one) for one in evidence.work],
        "rounds": [_text(one) for one in evidence.rounds],
        "round_ends": [
            [_text(began), _text(wrote)]
            for began, wrote in sorted(evidence.round_ends.items())
        ],
        "classifications": dict(sorted(evidence.classifications.items())),
        "reported": sorted(evidence.reported),
        "as_of": _text(evidence.as_of),
        "alert_fired_at": _optional_text(evidence.alert_fired_at),
        "alert_asked": evidence.alert_asked,
        "alert_already_firing": evidence.alert_already_firing,
    }


def _platform(evidence: PlatformEvidence) -> dict[str, object]:
    return {
        "kind": "platform",
        "first_heartbeat_after": _optional_text(evidence.first_heartbeat_after),
        "first_round_after": _optional_text(evidence.first_round_after),
        "reported_misses": list(evidence.reported_misses),
        "as_of": _text(evidence.as_of),
    }


def _work(one: StationWork) -> dict[str, object]:
    return (
        {
            "assignment_id": one.assignment_id,
            "pass_id": one.pass_id,
            "state": one.state,
            "revoked_reason": one.revoked_reason,
        }
        | {name: _text(getattr(one, name)) for name in _WORK_INSTANTS}
        | {name: _optional_text(getattr(one, name)) for name in _WORK_OPTIONAL}
        | {name: [_text(at) for at in getattr(one, name)] for name in _WORK_SERIES}
    )


def _evidence(row: Row) -> StationEvidence | PlatformEvidence:
    if row["kind"] == "platform":
        return PlatformEvidence(
            first_heartbeat_after=_optional_instant(row["first_heartbeat_after"]),
            first_round_after=_optional_instant(row["first_round_after"]),
            reported_misses=tuple(str(one) for one in _list(row, "reported_misses")),
            as_of=_instant(row["as_of"]),
        )
    classifications = row["classifications"]
    if not isinstance(classifications, Mapping):
        message = "classifications is not a table"
        raise TypeError(message)
    return StationEvidence(
        heartbeats=tuple(_instant(one) for one in _list(row, "heartbeats")),
        work=tuple(_work_from(one) for one in _list(row, "work")),
        rounds=tuple(_instant(one) for one in _list(row, "rounds")),
        round_ends={
            _instant(pair[0]): _instant(pair[1])
            for pair in (_pair(one) for one in _list(row, "round_ends"))
        },
        classifications={str(k): str(v) for k, v in classifications.items()},
        reported=frozenset(str(one) for one in _list(row, "reported")),
        as_of=_instant(row["as_of"]),
        alert_fired_at=_optional_instant(row["alert_fired_at"]),
        alert_asked=bool(row["alert_asked"]),
        alert_already_firing=bool(row["alert_already_firing"]),
    )


def _work_from(value: object) -> StationWork:
    if not isinstance(value, Mapping):
        message = f"a work entry is {value!r}, not a table"
        raise TypeError(message)
    revoked = value["revoked_reason"]
    return StationWork(
        assignment_id=str(value["assignment_id"]),
        pass_id=int(str(value["pass_id"])),
        decided_at=_instant(value["decided_at"]),
        start_at=_instant(value["start_at"]),
        end_at=_instant(value["end_at"]),
        state=str(value["state"]),
        revoked_reason=None if revoked is None else str(revoked),
        revoked_at=_optional_instant(value["revoked_at"]),
        redecided_at=_optional_instant(value["redecided_at"]),
        declined_at=_optional_instant(value["declined_at"]),
        offline_revocations=_instants(value, "offline_revocations"),
        revocations=_instants(value, "revocations"),
        reinstatements=_instants(value, "reinstatements"),
    )


def _instants(value: Mapping[str, object], name: str) -> tuple[datetime, ...]:
    return tuple(_instant(at) for at in _list(value, name))


def _index(row: Row) -> int:
    return int(str(row["index"]))


def _list(row: Mapping[str, object], name: str) -> list[object]:
    value = row[name]
    if not isinstance(value, list):
        message = f"{name} is {value!r}, not a list"
        raise TypeError(message)
    return value


def _pair(value: object) -> tuple[object, object]:
    if not isinstance(value, list) or len(value) != 2:  # noqa: PLR2004 — a pair
        message = f"{value!r} is not a pair"
        raise TypeError(message)
    return value[0], value[1]


def _text(value: datetime) -> str:
    return value.isoformat()


def _optional_text(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _instant(value: object) -> datetime:
    moment = datetime.fromisoformat(str(value))
    if moment.tzinfo is None:
        message = f"{value!r} carries no zone"
        raise ValueError(message)
    return moment


def _optional_instant(value: object) -> datetime | None:
    return None if value is None else _instant(value)
