"""``meridian.datasets.completeness`` — D-149 to D-151, case by case.

Built from typed rows in memory: completeness is a pure function of labelled
passes, computed archive passes and receptions, so no case needs a file.

Reference: docs/DECISIONS.md D-148, D-149, D-150, D-151.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta

from meridian.datasets.archive_matching import match_receptions
from meridian.datasets.completeness import (
    STATUSES,
    StationDay,
    archive_station_days,
    own_station_days,
    summarise,
)
from meridian.datasets.label_config import CompletenessConfig
from meridian.datasets.labels import LabelledPass
from meridian.datasets.snapshot_rows import ArchivePassRow, ArchiveReception

DAY = datetime(2026, 8, 14, tzinfo=UTC)
CONFIG = CompletenessConfig()

_ids = iter(range(1, 10_000))


def labelled(
    *,
    label: str | None = "successful_reception",
    excluded: str | None = None,
    reported: bool = True,
    station: str = "st_a",
    at: datetime = DAY + timedelta(hours=9),
) -> LabelledPass:
    """A labelled physical pass; excluded as ``simulated`` makes it simulated."""
    pass_id = next(_ids)
    return LabelledPass(
        pass_id=pass_id,
        pass_ids=(pass_id,),
        station_id=station,
        satellite_id="norad:57166",
        aos=at,
        los=at + timedelta(minutes=11),
        label=label,
        exclusion_reason=excluded,
        source_outcome="decoded" if reported else None,
        listening_confirmed=True,
        scheduled_by=("A",),
        simulated=excluded == "simulated",
    )


def not_scheduled() -> LabelledPass:
    return labelled(label=None, excluded="not_scheduled", reported=False)


def only(days: tuple[StationDay, ...]) -> StationDay:
    (one,) = days
    return one


# --- our stations ---------------------------------------------------------------


def test_four_of_five_attempted_is_exactly_the_default_threshold() -> None:
    passes = [labelled() for _ in range(4)] + [not_scheduled()]

    day = only(own_station_days(passes, CONFIG))

    assert (day.eligible, day.attempted) == (5, 4)
    assert day.completeness == 0.8
    assert day.status == "retained"


def test_three_of_five_is_below_it() -> None:
    passes = [labelled() for _ in range(3)] + [not_scheduled(), not_scheduled()]

    assert only(own_station_days(passes, CONFIG)).status == "below_threshold"


def test_what_is_not_an_opportunity_is_not_in_the_denominator() -> None:
    """Still settling, and a satellite that was not transmitting (D-149)."""
    passes = [
        labelled(),
        labelled(label=None, excluded="report_window_open", reported=False),
        labelled(label="satellite_silent", excluded="satellite_silent"),
    ]

    day = only(own_station_days(passes, CONFIG))

    assert (day.eligible, day.attempted) == (1, 1)


def test_not_knowing_whether_the_satellite_transmitted_stays_in() -> None:
    passes = [
        labelled(
            label="satellite_state_indeterminate",
            excluded="satellite_state_indeterminate",
        )
    ]

    day = only(own_station_days(passes, CONFIG))

    assert (day.eligible, day.attempted, day.usable) == (1, 1, 0)


def test_attempted_is_a_report_not_a_success() -> None:
    """A confirmed miss was attempted; a declined assignment was not (D-149)."""
    passes = [
        labelled(label="confirmed_miss"),
        labelled(label="assignment_declined", reported=False),
    ]

    day = only(own_station_days(passes, CONFIG))

    assert (day.eligible, day.attempted, day.usable) == (2, 1, 1)


def test_a_confirmed_silence_with_no_report_was_attempted() -> None:
    """Rule 7: the registry confirmed it listened, so it tried and heard nothing.

    Usable is never more than attempted: a miss the weights score is a pass
    the policy is counted as having taken.
    """
    passes = [
        labelled(label="confirmed_miss", reported=False),
        labelled(label="station_not_confirmed_listening", reported=False),
    ]

    day = only(own_station_days(passes, CONFIG))

    assert (day.eligible, day.attempted, day.usable) == (2, 1, 1)


def test_usable_counts_only_what_a_yield_label_can_score() -> None:
    passes = [
        labelled(label="successful_reception"),
        labelled(label="signal_no_decode"),
        labelled(label="station_not_confirmed_listening"),
    ]

    assert only(own_station_days(passes, CONFIG)).usable == 2


def test_a_simulated_station_has_no_station_days() -> None:
    passes = [labelled(station="st_sim", excluded="simulated")]

    assert own_station_days(passes, CONFIG) == ()


def test_a_day_with_nothing_eligible_is_empty_and_has_no_ratio() -> None:
    passes = [labelled(label=None, excluded="report_window_open", reported=False)]

    day = only(own_station_days(passes, CONFIG))

    assert day.status == "empty"
    assert day.completeness is None


def test_the_day_is_the_utc_date_of_acquisition() -> None:
    passes = [
        labelled(at=DAY - timedelta(minutes=1)),
        labelled(at=DAY + timedelta(minutes=1)),
    ]

    days = own_station_days(passes, CONFIG)

    assert [one.day for one in days] == [date(2026, 8, 13), date(2026, 8, 14)]


def test_rows_are_in_station_then_day_order() -> None:
    passes = [
        labelled(station="st_b"),
        labelled(station="st_a", at=DAY + timedelta(days=1)),
        labelled(station="st_a"),
    ]

    days = own_station_days(passes, CONFIG)

    assert [(one.station, one.day.day) for one in days] == [
        ("st_a", 14),
        ("st_a", 15),
        ("st_b", 14),
    ]


def test_every_row_is_plain_values() -> None:
    row = only(own_station_days([labelled()], CONFIG)).row()

    assert row == {
        "population": "own",
        "station": "st_a",
        "day": "2026-08-14",
        "eligible": 1,
        "attempted": 1,
        "usable": 1,
        "completeness": 1.0,
        "status": "retained",
    }


# --- archive stations -------------------------------------------------------------


def computed(
    hour: float, *, station: int = 7, peak: float = 40.0, day: datetime = DAY
) -> ArchivePassRow:
    aos = day + timedelta(hours=hour)
    return ArchivePassRow(
        archive_station_id=station,
        satellite_id="norad:25544",
        aos=aos,
        los=aos + timedelta(minutes=10),
        max_elevation_deg=peak,
    )


def archive_days(
    passes: Sequence[ArchivePassRow],
    receptions: Sequence[ArchiveReception],
    config: CompletenessConfig,
) -> tuple[tuple[StationDay, ...], int]:
    """The days, and how many receptions no computed pass could place."""
    matches = match_receptions(passes, receptions, config.archive_match_tolerance_s)
    return archive_station_days(passes, matches, config), matches.unmatched


def received(
    at: datetime,
    *,
    station: int = 7,
    outcome: str = "decoded",
    key: str = "norad:25544",
    kind: str = "norad",
) -> ArchiveReception:
    return ArchiveReception(
        satellite_key=key,
        satellite_key_kind=kind,
        started_at=at,
        archive_outcome=outcome,
        archive_station_id=station,
    )


def test_a_reception_inside_a_computed_window_attempts_it() -> None:
    passes = [computed(3), computed(9)]

    days, unmatched = archive_days(
        passes, [received(passes[0].aos + timedelta(minutes=2))], CONFIG
    )

    day = only(days)
    assert (day.station, day.eligible, day.attempted) == ("archive:7", 2, 1)
    assert unmatched == 0


def test_the_tolerance_reaches_before_acquisition_and_no_further() -> None:
    target = computed(3)
    tolerance = timedelta(seconds=CONFIG.archive_match_tolerance_s)

    inside, _ = archive_days([target], [received(target.aos - tolerance)], CONFIG)
    outside, unmatched = archive_days(
        [target],
        [received(target.aos - tolerance - timedelta(seconds=1))],
        CONFIG,
    )

    assert only(inside).attempted == 1
    assert only(outside).attempted == 0
    assert unmatched == 1


def test_the_nearest_acquisition_claims_a_reception_two_windows_could() -> None:
    """Both widened windows reach it; the one rising 90 s later is nearer.

    The earlier pass is below the floor, so the day shows one attempt only if
    the reception went to the later one.
    """
    config = CompletenessConfig(archive_min_elevation_deg=10.0)
    first = computed(3, peak=5.0)
    second = computed(3 + 13 / 60)
    at = first.los + timedelta(seconds=90)

    days, unmatched = archive_days([first, second], [received(at)], config)

    assert (only(days).eligible, only(days).attempted) == (1, 1)
    assert unmatched == 0


def test_a_pass_below_the_floor_is_not_available() -> None:
    config = CompletenessConfig(archive_min_elevation_deg=10.0)
    low, high = computed(3, peak=6.0), computed(9, peak=45.0)

    days, unmatched = archive_days(
        [low, high], [received(low.aos + timedelta(minutes=1))], config
    )

    assert (only(days).eligible, only(days).attempted) == (1, 0)
    assert unmatched == 0


def test_an_unknown_outcome_is_attempted_but_not_usable() -> None:
    target = computed(3)

    days, _ = archive_days([target], [received(target.aos, outcome="unknown")], CONFIG)

    assert (only(days).attempted, only(days).usable) == (1, 0)


def test_a_day_inside_the_span_with_no_reception_is_inactive_not_zero() -> None:
    """D-150: no heartbeat, so a silent day may be a station that was off."""
    passes = [computed(3, day=DAY + timedelta(days=n)) for n in range(3)]
    receptions = [received(passes[0].aos), received(passes[2].aos)]

    days, _ = archive_days(passes, receptions, CONFIG)

    assert [one.status for one in days] == ["retained", "inactive", "retained"]
    assert days[1].completeness is None
    assert days[1].eligible == 1


def test_days_outside_the_span_are_not_the_station_s() -> None:
    passes = [computed(3, day=DAY + timedelta(days=n)) for n in range(3)]

    days, _ = archive_days(passes, [received(passes[1].aos)], CONFIG)

    assert [one.day for one in days] == [date(2026, 8, 15)]


def test_a_reception_the_denominator_could_not_place_is_counted() -> None:
    """No computed pass for its station, or a key that is not a NORAD number."""
    target = computed(3)

    days, unmatched = archive_days(
        [target],
        [
            received(target.aos, station=99),
            received(target.aos, key="LUME-1", kind="source_name"),
        ],
        CONFIG,
    )

    assert unmatched == 2
    assert [one.station for one in days] == ["archive:7"]
    assert only(days).attempted == 0


# --- the summary ----------------------------------------------------------------


def a_day(attempted: int, eligible: int, n: int = 0, status: str = "") -> StationDay:
    reaches = eligible and attempted / eligible >= CONFIG.threshold
    ratio_status = status or ("retained" if reaches else "below_threshold")
    return StationDay(
        population="own",
        station=f"st_{n}",
        day=DAY.date(),
        eligible=eligible,
        attempted=attempted,
        usable=attempted,
        status=ratio_status,
    )


def test_every_status_is_counted_even_at_zero() -> None:
    summary = summarise([a_day(1, 1)], "own", CONFIG)

    assert summary.statuses == dict.fromkeys(STATUSES, 0) | {"retained": 1}


def test_the_histogram_bins_exact_tenths_by_integer_arithmetic() -> None:
    """7/10 in the 0.7 bin, 1.0 in the last, 0 in the first."""
    days = [a_day(7, 10), a_day(1, 1, 1), a_day(0, 3, 2), a_day(3, 10, 3)]

    histogram = summarise(days, "own", CONFIG).distribution.histogram

    assert histogram == (1, 0, 0, 1, 0, 0, 0, 1, 0, 1)


def test_deciles_are_nearest_rank_over_the_days_with_a_ratio() -> None:
    days = [a_day(n, 10, n) for n in range(1, 11)] + [a_day(0, 0, 99, status="empty")]

    distribution = summarise(days, "own", CONFIG).distribution

    assert distribution.count == 10
    assert distribution.deciles == (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)


def test_sensitivity_states_retained_and_excluded_at_every_threshold() -> None:
    days = [a_day(n, 10, n) for n in (5, 6, 7, 8, 9)]

    summary = summarise(days, "own", CONFIG)

    assert summary.sensitivity == (
        (0.5, 5, 0),
        (0.6, 4, 1),
        (0.7, 3, 2),
        (0.8, 2, 3),
        (0.9, 1, 4),
    )


def test_a_population_with_no_days_still_says_so() -> None:
    summary = summarise([a_day(1, 1)], "archive", CONFIG)

    assert summary.distribution.count == 0
    assert summary.distribution.deciles == ()
    assert set(summary.statuses.values()) == {0}


def test_the_populations_are_never_pooled() -> None:
    archive = StationDay(
        population="archive",
        station="archive:7",
        day=DAY.date(),
        eligible=10,
        attempted=1,
        usable=1,
        status="below_threshold",
    )

    own = summarise([a_day(9, 10), archive], "own", CONFIG)

    assert (own.eligible, own.attempted) == (10, 9)


def test_the_summary_renders_as_plain_values() -> None:
    parameters = summarise([a_day(4, 5)], "own", CONFIG).parameters()

    assert parameters["station_days"] == dict.fromkeys(STATUSES, 0) | {"retained": 1}
    assert isinstance(parameters["sensitivity"], list)
    assert parameters["sensitivity"][3] == {
        "threshold": 0.8,
        "retained": 1,
        "excluded": 0,
    }


def test_an_inactive_day_s_passes_are_in_no_total() -> None:
    """D-150: the day's row keeps its count; the population's totals do not."""
    inactive = a_day(0, 3, n=1, status="inactive")

    summary = summarise([a_day(2, 3), inactive], "own", CONFIG)

    assert (summary.eligible, summary.attempted) == (3, 2)
    assert summary.statuses["inactive"] == 1
