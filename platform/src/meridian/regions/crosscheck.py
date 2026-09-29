"""Our receptions against the public layers: a check on both chains (D-233).

Two questions, each with an answer that is wrong in a way we can see:

**Did the ingest keep up with the receiving chain?** On every UTC day one of
our decoded receptions imaged the area, each daily public product should have
a value for the area too. A day with our imagery and no public value is a gap
in *the ingest* — a source that was down, a timer that did not run, a box that
did not cover the area — listed by day so it can be chased.

**Does the receiving chain behave the same whatever the weather?** At 137 MHz
rain and cloud do not attenuate the downlink to any degree a decode would
notice. So among the passes our stations inside the area attempted while
confirmed listening (rule 7), the decode rate on wet days and on dry days
should agree. If their intervals do not overlap, the difference is not the
sky's; it points at *the station* — water in a connector, a feedline that
detunes when wet — and says which days to look at.

Neither answer is a verdict on its own: each is a rate with a Wilson interval
and its counts, and "insufficient" where the counts are too thin to say.

Reference: docs/DECISIONS.md D-145, D-233; CLAUDE.md rule 7.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from meridian.datasets.weighting import Rate, wilson
from meridian.regions.coverage import DECODED, Covering
from meridian.regions.geometry import Polygon
from meridian.regions.rows import Reception
from meridian.regions.series import SeriesPoint

__all__ = [
    "ATTEMPTED",
    "DAILY",
    "IngestAgreement",
    "WeatherAgreement",
    "ingest_agreement",
    "weather_agreement",
]

DAILY = ("precipitation", "aerosol_optical_depth", "cloud_cover", "fire_count")
ATTEMPTED = frozenset({"decoded", "signal_no_decode", "no_signal"})
"""Outcomes of a pass the station actually worked; ``aborted`` and
``not_attempted`` say nothing about the chain's sensitivity."""


@dataclass(frozen=True, slots=True)
class IngestAgreement:
    """For one daily product: on how many of our imaged days it had a value."""

    area_id: int
    quantity: str
    days: int
    present: int
    rate: Rate | None
    missing_days: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WeatherAgreement:
    """Decode rates on wet and dry days, for stations inside the area."""

    area_id: int
    wet: Rate | None
    dry: Rate | None
    wet_attempts: int
    dry_attempts: int
    unknown_days: int
    verdict: str
    """``consistent``, ``differs`` or ``insufficient``."""


def ingest_agreement(
    area_id: int, covering: Sequence[Covering], points: Sequence[SeriesPoint]
) -> list[IngestAgreement]:
    """How complete each daily product was on the days we imaged the area.

    Measured receptions only: a simulated reception imaged nothing real.
    """
    days = sorted({_day(one.started_at) for one in covering if not one.simulated})
    out = []
    for quantity in DAILY:
        held = {
            _day(one.period_from)
            for one in points
            if one.quantity == quantity and one.value is not None
        }
        missing = tuple(day for day in days if day not in held)
        present = len(days) - len(missing)
        out.append(
            IngestAgreement(
                area_id=area_id,
                quantity=quantity,
                days=len(days),
                present=present,
                rate=wilson(present / len(days), len(days)) if days else None,
                missing_days=missing,
            )
        )
    return out


def weather_agreement(  # noqa: PLR0913 — each input is a separate fact the check needs
    area_id: int,
    polygon: Polygon,
    receptions: Sequence[Reception],
    stations: Mapping[str, tuple[float, float]],
    points: Sequence[SeriesPoint],
    *,
    wet_day_mm: float,
    min_points: int,
) -> WeatherAgreement:
    """Decode rate on wet and dry days, with intervals, and whether they agree."""
    rain = {
        _day(one.period_from): one.value
        for one in points
        if one.quantity == "precipitation" and one.value is not None
    }
    wet = dry = wet_ok = dry_ok = unknown = 0
    for one in receptions:
        place = stations.get(one.station_id)
        if (
            one.simulated
            or one.outcome not in ATTEMPTED
            or one.listening_confirmed is not True
            or place is None
            or not polygon.contains(*place)
        ):
            continue
        amount = rain.get(_day(one.started_at))
        decoded = one.outcome == DECODED
        if amount is None:
            unknown += 1
        elif amount >= wet_day_mm:
            wet, wet_ok = wet + 1, wet_ok + decoded
        else:
            dry, dry_ok = dry + 1, dry_ok + decoded
    wet_rate = wilson(wet_ok / wet, wet) if wet else None
    dry_rate = wilson(dry_ok / dry, dry) if dry else None
    return WeatherAgreement(
        area_id=area_id,
        wet=wet_rate,
        dry=dry_rate,
        wet_attempts=wet,
        dry_attempts=dry,
        unknown_days=unknown,
        verdict=_verdict(wet_rate, dry_rate, min_points),
    )


def _verdict(wet: Rate | None, dry: Rate | None, min_points: int) -> str:
    if wet is None or dry is None or min(wet.n, dry.n) < min_points:
        return "insufficient"
    return "differs" if wet.high < dry.low or dry.high < wet.low else "consistent"


def _day(moment: datetime) -> str:
    return moment.astimezone(UTC).date().isoformat()
