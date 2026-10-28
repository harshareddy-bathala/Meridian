"""From named rows to facts: a report read exactly as it was the first time.

A report's ``inputs_json`` names its rows (D-302): a pass report one
reception's observation revision, verdict method and diagnosis; a weekly report
every pass of the week, the same way, and the warnings open at its end. This
module turns those names into the stored rows and the rows into facts, and
nothing else: queuing a report and regenerating one both come through here, so
the two cannot read differently.

Reference: docs/DECISIONS.md D-302, D-306.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta

from meridian.notifications.facts import (
    CauseTested,
    ChainWarningFact,
    DiagnosisFact,
    Facts,
    PassFacts,
    VerdictFact,
    WeeklyFacts,
)
from meridian.notifications.wording import (
    OUTCOMES,
    UNSCORED,
    VERDICT_BUCKETS,
    verdict_bucket,
)
from meridian.store.report_reads import PassRow, ReceptionKey, read_pass
from meridian.store.stations import Connection
from meridian.store.week_reads import (
    hours_heard,
    read_week_receptions,
    read_week_station,
    read_week_warnings,
)

__all__ = [
    "WEEK",
    "facts_for",
    "pass_facts",
    "pass_inputs",
    "week_facts",
    "week_inputs",
]

WEEK = timedelta(days=7)


def pass_inputs(key: ReceptionKey) -> dict[str, object]:
    """What a pass report names, as stored in ``inputs_json``."""
    return {"reception": _key_json(key)}


def week_inputs(keys: list[ReceptionKey], warnings: list[int]) -> dict[str, object]:
    """What a weekly report names, as stored in ``inputs_json``."""
    return {"receptions": [_key_json(one) for one in keys], "warnings": warnings}


def pass_facts(conn: Connection, inputs: dict[str, object]) -> PassFacts:
    """The facts of a pass report, from exactly the rows it names.

    Raises:
        LookupError: A named row is not stored.
        TypeError: ``inputs`` is not a pass report's.
    """
    row = read_pass(conn, _key_of(inputs.get("reception")))
    return PassFacts(
        station_id=row.station_id,
        station_name=row.station_name,
        simulated=row.simulated,
        satellite_id=row.satellite_id,
        satellite_name=row.satellite_name,
        window_start=row.start_at,
        window_end=row.end_at,
        max_elevation_deg=row.max_elevation_deg,
        outcome=row.outcome,
        peak_snr_db=row.peak_snr_db,
        frames_decoded=row.frames_decoded,
        verdict=_verdict(row),
        diagnosis=_diagnosis(row),
    )


def week_facts(
    conn: Connection,
    station_id: str,
    period_start: datetime,
    inputs: dict[str, object],
) -> WeeklyFacts:
    """The facts of a weekly report, from exactly the rows it names.

    Raises:
        LookupError: A named row is not stored.
        TypeError: ``inputs`` is not a weekly report's.
    """
    raw_keys, raw_warnings = inputs.get("receptions"), inputs.get("warnings")
    if not isinstance(raw_keys, list) or not isinstance(raw_warnings, list):
        raise TypeError("a weekly report names its receptions and its warnings")
    keys = [_key_of(one) for one in raw_keys]
    end = period_start + WEEK
    station = read_week_station(conn, station_id)
    receptions = read_week_receptions(conn, keys)
    warnings = read_week_warnings(conn, [int(str(one)) for one in raw_warnings])

    outcomes = Counter(one.outcome or "no_report" for one in receptions)
    verdicts = Counter(
        UNSCORED
        if one.probability_usable is None
        else verdict_bucket(one.probability_usable)
        for one in receptions
        if one.outcome == "decoded"
    )
    causes = Counter(one.cause for one in receptions if one.cause is not None)
    buckets = [words for _, words in VERDICT_BUCKETS] + [UNSCORED]
    return WeeklyFacts(
        station_id=station.station_id,
        station_name=station.name,
        simulated=station.simulated,
        period_start=period_start,
        period_end=end,
        hours_heard=hours_heard(conn, station_id, period_start, end),
        hours=int(WEEK.total_seconds()) // 3_600,
        passes=len(receptions),
        outcomes=tuple((key, outcomes[key]) for key in OUTCOMES if outcomes[key]),
        verdicts=tuple((key, verdicts[key]) for key in buckets if verdicts[key]),
        causes=tuple(sorted(causes.items(), key=lambda item: (-item[1], item[0]))),
        warnings=tuple(
            ChainWarningFact(
                band=one.band,
                freq_min_hz=one.freq_min_hz,
                freq_max_hz=one.freq_max_hz,
                raised_at=one.raised_at,
                shortfall_db=one.shortfall_db,
            )
            for one in warnings
        ),
    )


def facts_for(
    conn: Connection,
    *,
    kind: str,
    station_id: str,
    period_start: datetime | None,
    inputs: dict[str, object],
) -> Facts:
    """The facts of a stored report, whichever kind it is.

    Raises:
        LookupError: A named row is not stored.
        ValueError: The report's parts do not fit its kind.
    """
    if kind == "pass":
        return pass_facts(conn, inputs)
    if kind == "weekly" and period_start is not None:
        return week_facts(conn, station_id, period_start.astimezone(UTC), inputs)
    raise ValueError(f"a {kind} report cannot be read")


def _verdict(row: PassRow) -> VerdictFact | None:
    if row.probability_usable is None or row.partial_below is None:
        return None
    return VerdictFact(
        probability_usable=row.probability_usable,
        partial_below=row.partial_below,
        method=str(row.verdict_method),
    )


def _diagnosis(row: PassRow) -> DiagnosisFact | None:
    if row.cause is None:
        return None
    return DiagnosisFact(
        cause=row.cause,
        tested=tuple(
            CauseTested(cause=str(one.get("cause")), fired=bool(one.get("fired")))
            for one in row.candidates_json or []
        ),
        method=str(row.diagnosis_method),
    )


def _key_json(key: ReceptionKey) -> dict[str, object]:
    return {
        "assignment_id": key.assignment_id,
        "revision": key.revision,
        "verdict_method": key.verdict_method,
        "diagnosis_id": key.diagnosis_id,
    }


def _key_of(raw: object) -> ReceptionKey:
    if not isinstance(raw, dict) or not isinstance(raw.get("assignment_id"), str):
        raise TypeError(f"not a reception's key: {raw!r}")
    revision, verdict, diagnosis = (
        raw.get("revision"),
        raw.get("verdict_method"),
        raw.get("diagnosis_id"),
    )
    return ReceptionKey(
        assignment_id=str(raw["assignment_id"]),
        revision=None if revision is None else int(str(revision)),
        verdict_method=None if verdict is None else str(verdict),
        diagnosis_id=None if diagnosis is None else int(str(diagnosis)),
    )
