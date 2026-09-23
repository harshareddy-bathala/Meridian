"""``meridian.datasets.physical_passes`` — D-148's grouping, case by case.

Reference: docs/DECISIONS.md D-063, D-148.
"""

from __future__ import annotations

import random
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from meridian.datasets.physical_passes import group_physical_passes
from meridian.datasets.snapshot_rows import PassRow

AOS = datetime(2026, 9, 20, 9, 41, tzinfo=UTC)
EPOCH = AOS - timedelta(days=1)


def prediction(
    pass_id: int,
    *,
    shift_s: float = 0,
    epoch: datetime = EPOCH,
    station: str = "st_a",
    satellite: str = "norad:57166",
) -> PassRow:
    """By default computed a minute after its elements' epoch."""
    aos = AOS + timedelta(seconds=shift_s)
    return PassRow(
        pass_id=pass_id,
        station_id=station,
        satellite_id=satellite,
        aos=aos,
        los=aos + timedelta(minutes=11),
        max_elevation_deg=40.0,
        element_set_epoch=epoch,
        computed_at=epoch + timedelta(minutes=1),
        simulated=False,
    )


def test_two_predictions_of_one_rise_are_one_pass() -> None:
    """Two element sets, acquisitions seconds apart: the case D-063 creates daily."""
    (physical,) = group_physical_passes(
        [prediction(1), prediction(2, shift_s=3.2, epoch=EPOCH + timedelta(hours=8))]
    )

    assert physical.pass_ids == (1, 2)


def test_the_next_rise_is_another_pass() -> None:
    first, second = group_physical_passes(
        [prediction(1), prediction(2, shift_s=100 * 60)]
    )

    assert (first.pass_ids, second.pass_ids) == ((1,), (2,))


def test_a_window_that_starts_as_the_last_one_ends_is_the_next_rise() -> None:
    """Half-open windows, as everywhere else: touching is not overlapping."""
    grouped = group_physical_passes([prediction(1), prediction(2, shift_s=11 * 60)])

    assert [one.pass_ids for one in grouped] == [(1,), (2,)]


def test_overlap_chains_into_one_pass() -> None:
    """A overlaps B and B overlaps C, though A and C do not touch."""
    six_minutes = [
        replace(one, los=one.aos + timedelta(minutes=6))
        for one in (
            prediction(1),
            prediction(2, shift_s=5 * 60),
            prediction(3, shift_s=10 * 60),
        )
    ]

    (physical,) = group_physical_passes(six_minutes)

    assert physical.pass_ids == (1, 2, 3)
    assert physical.last_los == AOS + timedelta(minutes=16)


def test_another_station_or_satellite_is_never_the_same_pass() -> None:
    grouped = group_physical_passes(
        [
            prediction(1),
            prediction(2, station="st_b"),
            prediction(3, satellite="norad:40069"),
        ]
    )

    assert [one.pass_ids for one in grouped] == [(1,), (2,), (3,)]


def test_the_representative_is_the_newest_prediction_made_before_the_rise() -> None:
    grouped = group_physical_passes(
        [
            prediction(1, epoch=EPOCH),
            prediction(2, shift_s=2, epoch=EPOCH + timedelta(hours=12)),
            prediction(3, shift_s=1, epoch=AOS + timedelta(hours=1)),
        ]
    )

    assert grouped[0].representative.pass_id == 2


def test_an_early_epoch_published_after_the_pass_is_not_before_it() -> None:
    """Elements from an hour before the rise, retrieved and computed after it.

    By epoch it is the newest before the pass; by what was known then it did
    not exist yet, so the older prediction represents the rise (D-148).
    """
    grouped = group_physical_passes(
        [
            prediction(1, epoch=EPOCH),
            replace(
                prediction(2, shift_s=2, epoch=AOS - timedelta(hours=1)),
                computed_at=AOS + timedelta(hours=3),
            ),
        ]
    )

    assert grouped[0].representative.pass_id == 1


def test_a_tie_on_epoch_goes_to_the_lowest_pass_id() -> None:
    (physical,) = group_physical_passes([prediction(5), prediction(4, shift_s=1)])

    assert physical.representative.pass_id == 4


def test_with_no_prediction_from_before_the_rise_the_first_made_represents_it() -> None:
    later = AOS + timedelta(hours=2)
    (physical,) = group_physical_passes(
        [
            prediction(1, epoch=later + timedelta(hours=5)),
            prediction(2, shift_s=1, epoch=later),
        ]
    )

    assert physical.representative.pass_id == 2


def test_the_order_predictions_arrive_in_does_not_matter() -> None:
    predictions = [
        prediction(1),
        prediction(2, shift_s=2, epoch=EPOCH + timedelta(hours=3)),
        prediction(3, shift_s=100 * 60),
        prediction(4, station="st_b"),
    ]
    shuffled = random.Random(11).sample(predictions, len(predictions))

    assert group_physical_passes(shuffled) == group_physical_passes(predictions)
