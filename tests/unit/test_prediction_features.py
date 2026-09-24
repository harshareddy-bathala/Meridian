"""``meridian.prediction`` features — geometry, station history, and the past only.

Most cases build labelled passes and geometry by hand, so each number is the
one a reader can work out. The first cases read an evaluation dataset and its
raw snapshot back from disk, so the readers are known to agree with what
``label`` and ``export`` write.

The centre of the file is D-157's leak test: every outcome that had not
settled when a pass began is changed, and that pass's features are unchanged
to the byte. Its positive control changes one outcome that *had* settled, and
the features move.

Reference: docs/DECISIONS.md D-148, D-157, D-159, D-160, D-161.
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from meridian.datasets.canonical import canonical_line
from meridian.datasets.evaluation import build_evaluation_dataset
from meridian.datasets.label_config import LabelConfig
from meridian.datasets.label_rows import LABELS_FILE, read_labels
from meridian.datasets.labels import LabelledPass
from meridian.datasets.publish import read_directory
from meridian.datasets.row_fields import MalformedSnapshotError
from meridian.prediction.feature_rows import (
    FeatureRows,
    PassGeometry,
    PassTrack,
    band_of,
    read_feature_rows,
)
from meridian.prediction.features import FEATURES, RECENT, compute_features
from meridian.prediction.history import History, events_of
from meridian.prediction.profiles import Environment

DAY0 = datetime(2026, 9, 1, 6, 0, tzinfo=UTC)
MARGIN_S = 24 * 3600
SATELLITE = "norad:57166"
EPOCH = DAY0 - timedelta(hours=12)
CREATED = datetime(2026, 9, 23, 7, 0, tzinfo=UTC)


def labelled(
    pass_id: int,
    day: int,
    label: str | None = "successful_reception",
    **fields: Any,
) -> LabelledPass:
    aos = DAY0 + timedelta(days=day)
    base = LabelledPass(
        pass_id=pass_id,
        pass_ids=(pass_id,),
        station_id="st_a",
        satellite_id=SATELLITE,
        aos=aos,
        los=aos + timedelta(minutes=11),
        label=label,
        exclusion_reason=None,
        source_outcome=None,
        listening_confirmed=True,
        scheduled_by=("A",),
        simulated=False,
    )
    return replace(base, **fields)


def geometry(**fields: Any) -> PassGeometry:
    base = PassGeometry(
        aos=DAY0,
        computed_at=EPOCH,
        max_elevation_deg=40.0,
        aos_azimuth_deg=90.0,
        los_azimuth_deg=270.0,
        element_set_epoch=EPOCH,
        track=None,
    )
    return replace(base, **fields)


def rows_for(passes: list[LabelledPass], **fields: Any) -> FeatureRows:
    return FeatureRows(
        geometry={one.pass_id: geometry(**fields) for one in passes},
        bands={SATELLITE: "vhf"},
        longitudes={},
        readings={},
    )


def compute(
    targets: list[LabelledPass], rows: FeatureRows, known: list[LabelledPass]
) -> tuple[Any, ...]:
    """Features of ``targets``, against the history and environment of ``known``."""
    return compute_features(
        targets,
        rows,
        history_of(known),
        Environment(known, rows, settle_margin_s=MARGIN_S),
    )


def features(passes: list[LabelledPass]) -> list[dict[str, float]]:
    return [one.named() for one in compute(passes, rows_for(passes), passes)]


def alternating(days: int) -> list[LabelledPass]:
    """One pass a day; decoded on even days, a confirmed miss on odd ones."""
    return [
        labelled(n, n, "successful_reception" if n % 2 == 0 else "confirmed_miss")
        for n in range(days)
    ]


# --- reading them back ----------------------------------------------------------


def test_labels_read_back_are_the_labels_written(
    raw_snapshot: Any, archive_world: Any, datasets_root: Path
) -> None:
    published = build_evaluation_dataset(
        read_directory(raw_snapshot(archive_world)),
        LabelConfig(),
        root=datasets_root,
        created_at=CREATED,
    )
    files = read_directory(published.path).files

    labels = read_labels(files)

    assert labels
    assert b"".join(canonical_line(one.row()) for one in labels) == files[LABELS_FILE]


def test_feature_rows_read_geometry_and_bands(raw_snapshot: Any, world: Any) -> None:
    files = read_directory(raw_snapshot(world)).files

    rows = read_feature_rows(files)

    assert rows.geometry[1].aos_azimuth_deg == 350.0
    assert rows.geometry[1].element_set_epoch == datetime(2026, 9, 20, tzinfo=UTC)
    assert rows.geometry[1].track is None
    assert rows.bands == {SATELLITE: "vhf"}


def test_a_snapshot_without_tracks_is_sent_back_for_export(
    raw_snapshot: Any, world: Any
) -> None:
    files = dict(read_directory(raw_snapshot(world)).files)
    del files["pass_tracks.jsonl"]

    with pytest.raises(MalformedSnapshotError, match=r"before Stage 17.*export again"):
        read_feature_rows(files)


@pytest.mark.parametrize(
    ("hertz", "band"),
    [(137.9e6, "vhf"), (435e6, "uhf"), (1.7e9, "l"), (2.2e9, "s"), (8.2e9, "x")],
)
def test_bands(hertz: float, band: str) -> None:
    assert band_of(hertz) == band


# --- history, and only the past ----------------------------------------------


def test_a_pass_sees_only_outcomes_settled_before_it_began() -> None:
    """Daily passes, 24 h margin: day k's pass sees days 0 to k-2, never k-1."""
    named = features(alternating(6))

    assert [one["station_decode_n"] for one in named] == [0, 0, 1, 2, 3, 4]
    # Days 0..3 are success, miss, success, miss: 2 of 4, shrunk to 3/6.
    assert named[5]["station_decode_rate"] == pytest.approx(3 / 6)


def test_an_outcome_settles_at_exactly_los_plus_the_margin() -> None:
    first = labelled(1, 0)
    on_the_edge = labelled(2, 1, aos=first.los + timedelta(seconds=MARGIN_S))

    named = features([first, on_the_edge])

    assert named[1]["station_decode_n"] == 1


def test_the_recent_rate_is_over_the_last_recent_outcomes_only() -> None:
    passes = [labelled(n, n, "confirmed_miss") for n in range(RECENT)]
    passes += [labelled(100 + n, RECENT + n) for n in range(RECENT + 2)]

    last = features(passes)[-1]

    assert last["station_decode_n"] == RECENT
    assert last["station_decode_rate"] == pytest.approx((RECENT + 1) / (RECENT + 2))
    assert last["satellite_decode_n"] == 2 * RECENT


def test_availability_counts_scheduled_passes_the_station_took_up() -> None:
    passes = [
        labelled(1, 0, "assignment_declined"),
        labelled(2, 1, "station_unavailable"),
        labelled(3, 2, "satellite_silent", exclusion_reason="satellite_silent"),
        labelled(4, 5),
    ]

    last = features(passes)[-1]

    assert last["station_availability_n"] == 3
    assert last["station_availability"] == pytest.approx((1 + 1) / (3 + 2))
    assert last["station_decode_n"] == 0


def test_simulated_and_unscheduled_passes_are_no_one_s_history() -> None:
    passes = [
        labelled(1, 0, simulated=True, exclusion_reason="simulated"),
        labelled(2, 1, None, exclusion_reason="not_scheduled"),
        labelled(3, 5),
    ]

    last = features(passes)[-1]

    assert last["station_decode_n"] == 0
    assert last["station_availability_n"] == 0


def test_other_stations_and_satellites_keep_their_own_history() -> None:
    passes = [
        labelled(1, 0, station_id="st_b"),
        labelled(2, 1, satellite_id="norad:25544"),
        labelled(3, 5),
    ]

    last = features(passes)[-1]

    assert last["station_decode_n"] == 1
    assert last["satellite_decode_n"] == 0
    assert last["band_decode_n"] == 0


# --- D-157: the leak test -----------------------------------------------------

FLIP = {
    "successful_reception": "confirmed_miss",
    "confirmed_miss": "successful_reception",
}


def test_no_outcome_after_a_pass_began_reaches_its_features() -> None:
    """Every unsettled outcome changed, the pass itself included: identical bytes."""
    passes = alternating(12)
    before = [
        canonical_line({"values": list(one.values)})
        for one in compute(passes, rows_for(passes), passes)
    ]

    for index, target in enumerate(passes):
        changed = [
            replace(one, label=FLIP[str(one.label)])
            if one.los + timedelta(seconds=MARGIN_S) > target.aos
            else one
            for one in passes
        ]
        (after,) = compute([target], rows_for(changed), changed)
        assert canonical_line({"values": list(after.values)}) == before[index]


def test_a_settled_outcome_does_reach_them() -> None:
    """The positive control: the test above can fail."""
    passes = alternating(12)
    target = passes[-1]
    changed = [replace(passes[0], label=FLIP[str(passes[0].label)]), *passes[1:]]

    (before,) = compute([target], rows_for(passes), passes)
    (after,) = compute([target], rows_for(changed), changed)

    assert before.values != after.values


def history_of(passes: list[LabelledPass]) -> History:
    return History(
        events_of(passes, bands={SATELLITE: "vhf"}, settle_margin_s=MARGIN_S)
    )


# --- geometry ------------------------------------------------------------------


def test_angles_are_written_on_the_circle() -> None:
    (named,) = features([labelled(1, 0)])

    assert named["aos_azimuth_sin"] == pytest.approx(1.0)
    assert named["aos_azimuth_cos"] == pytest.approx(0.0, abs=1e-12)
    assert named["los_azimuth_sin"] == pytest.approx(-1.0)
    assert named["duration_min"] == pytest.approx(11.0)
    assert named["element_set_age_h"] == pytest.approx(12.0)
    assert named["max_elevation_deg"] == 40.0


def test_without_a_track_the_peak_is_the_short_way_between_rise_and_set() -> None:
    """A pass rising at 350° and setting at 10° crosses north, not south."""
    one = labelled(1, 0)
    rows = FeatureRows(
        geometry={1: geometry(aos_azimuth_deg=350.0, los_azimuth_deg=10.0)},
        bands={},
        longitudes={},
        readings={},
    )

    (vector,) = compute([one], rows, [])
    named = vector.named()

    assert named["peak_azimuth_cos"] == pytest.approx(1.0)
    assert named["azimuth_sweep_deg"] == pytest.approx(20.0)
    assert named["track_known"] == 0.0


def test_with_a_track_the_peak_and_sweep_come_from_it() -> None:
    track = PassTrack(
        start=DAY0,
        step_s=30,
        azimuth_deg=(350.0, 355.0, 5.0, 90.0),
        elevation_deg=(0.0, 20.0, 60.0, 10.0),
    )
    rows = FeatureRows(
        geometry={1: geometry(track=track)}, bands={}, longitudes={}, readings={}
    )

    (vector,) = compute([labelled(1, 0)], rows, [])
    named = vector.named()

    assert named["peak_azimuth_sin"] == pytest.approx(math.sin(math.radians(5.0)))
    assert named["azimuth_sweep_deg"] == pytest.approx(5.0 + 10.0 + 85.0)
    assert named["track_known"] == 1.0


def test_a_new_station_s_features_are_all_finite() -> None:
    """D-161's precondition: no history is one half and a zero count, never NaN."""
    (vector,) = compute(
        [labelled(1, 0, station_id="st_new")], rows_for([labelled(1, 0)]), []
    )

    assert len(vector.values) == len(FEATURES)
    assert all(math.isfinite(one) for one in vector.values)
    assert vector.named()["station_decode_rate"] == 0.5
    assert vector.named()["band_decode_n"] == 0.0


def test_a_labelled_pass_missing_from_the_snapshot_is_refused() -> None:
    with pytest.raises(MalformedSnapshotError, match="not a pair"):
        compute([labelled(9, 0)], rows_for([]), [])


def test_every_feature_is_in_a_known_group_and_named_once() -> None:
    assert {one.group for one in FEATURES} == {"elevation", "geometry", "ours"}
    assert len({one.name for one in FEATURES}) == len(FEATURES)
