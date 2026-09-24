"""``meridian.datasets.selection`` — what ``label`` now writes about selection.

Run end to end through :func:`build_evaluation_dataset` over the hand-built
raw snapshots of ``tests/unit/conftest.py``: :data:`ARCHIVE_WORLD`'s archive
station is 2/3 complete on its first day, inactive on its second, and
attempted-but-unscorable on its third.

Reference: docs/DECISIONS.md D-149, D-150, D-152, D-153, D-154.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from meridian.datasets.evaluation import (
    PROPENSITIES,
    STATION_DAYS,
    build_evaluation_dataset,
)
from meridian.datasets.label_config import LabelConfig
from meridian.datasets.publish import read_directory
from meridian.datasets.selection import NO_ELIGIBLE_PASSES, select
from meridian.datasets.snapshot_rows import (
    ArchivePassRow,
    ArchiveReception,
    SnapshotRows,
)

CREATED = datetime(2026, 9, 23, 7, 0, tzinfo=UTC)


def build(raw: Path, root: Path) -> Any:
    return build_evaluation_dataset(
        read_directory(raw), LabelConfig(), root=root, created_at=CREATED
    )


def lines(path: Path, name: str) -> list[dict[str, Any]]:
    return [json.loads(one) for one in (path / name).read_bytes().splitlines()]


@pytest.fixture
def dataset(raw_snapshot: Any, datasets_root: Path, archive_world: Any) -> Any:
    return build(raw_snapshot(archive_world), datasets_root)


def test_every_station_day_of_both_populations_is_written(dataset: Any) -> None:
    days = {
        (one["station"], one["day"]): (one["eligible"], one["attempted"], one["status"])
        for one in lines(dataset.path, STATION_DAYS)
    }

    assert days == {
        ("st_a", "2026-09-20"): (1, 1, "retained"),
        ("st_b", "2026-09-20"): (1, 1, "retained"),
        ("archive:7", "2026-09-10"): (3, 2, "below_threshold"),
        ("archive:7", "2026-09-11"): (1, 0, "inactive"),
        ("archive:7", "2026-09-12"): (1, 1, "retained"),
    }


def test_an_inactive_day_offers_no_candidate(dataset: Any) -> None:
    """D-150: a day the station may have been off is not a day it declined."""
    archive = [
        one
        for one in lines(dataset.path, PROPENSITIES)
        if one["population"] == "archive"
    ]

    assert len(archive) == 4
    assert not [one for one in archive if one["aos"].startswith("2026-09-11")]


def test_simulated_passes_are_neither_days_nor_candidates(dataset: Any) -> None:
    for name in (STATION_DAYS, PROPENSITIES):
        assert "st_sim" not in {one["station"] for one in lines(dataset.path, name)}


def test_the_propensity_file_carries_no_outcome(dataset: Any) -> None:
    """D-152: the decision and its cell, never what the pass returned."""
    rows = lines(dataset.path, PROPENSITIES)

    assert set(rows[0]) == {
        "population",
        "station",
        "satellite_id",
        "aos",
        "max_elevation_deg",
        "attempted",
        "model",
        "level",
        "cell",
        "cell_available",
        "cell_attempted",
        "propensity",
        "weight",
    }
    assert {one["weight"] for one in rows if not one["attempted"]} == {None}


def test_the_archive_s_positivity_problem_is_reported(dataset: Any) -> None:
    """Every 40° pass was taken and the 10° one was not: certain and unsupported."""
    weighting = dataset.manifest.summary["populations"]["archive"]["weighting"]

    assert (weighting["available"], weighting["certain"], weighting["unsupported"]) == (
        4,
        3,
        1,
    )


def test_an_archive_success_is_a_decoded_reception(dataset: Any) -> None:
    """Decoded and no_data are scored; unknown is attempted but not usable."""
    weighting = dataset.manifest.summary["populations"]["archive"]["weighting"]

    assert weighting["weighted"] == 2
    assert weighting["unweighted"]["estimate"] == 0.5


def test_our_stations_are_weighted_and_flagged(dataset: Any) -> None:
    weighting = dataset.manifest.summary["populations"]["own"]["weighting"]

    assert weighting["model"] == "binned-1"
    assert weighting["weighted"] == 2
    assert weighting["unreliable"] is True


def test_a_population_with_nothing_eligible_says_why(
    raw_snapshot: Any, datasets_root: Path, world: Any
) -> None:
    published = build(raw_snapshot(world), datasets_root)

    archive = published.manifest.summary["populations"]["archive"]

    assert archive["weighting"] == {"not_weighted": NO_ELIGIBLE_PASSES}
    assert archive["completeness"]["station_days"] == dict.fromkeys(
        ("retained", "below_threshold", "empty", "inactive"), 0
    )


def test_the_counts_name_every_station_day_status(dataset: Any) -> None:
    counts = dataset.manifest.counts

    assert counts["station_days.archive.inactive"] == 1
    assert counts["station_days.archive.below_threshold"] == 1
    assert counts["station_days.own.empty"] == 0
    assert counts["archive_receptions.unmatched"] == 0


def test_the_summary_is_part_of_the_hash(dataset: Any) -> None:
    """Read back from disk, the summary is what was written and what was hashed."""
    again = read_directory(dataset.path)

    assert again.manifest.summary == dataset.manifest.summary


def test_an_archive_pass_before_since_places_a_reception_but_is_not_available() -> None:
    """D-148, D-150: computed so its reception is not unmatched; in no ratio."""
    noon = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)

    def computed(aos: datetime) -> ArchivePassRow:
        return ArchivePassRow(
            archive_station_id=7,
            satellite_id="norad:25544",
            aos=aos,
            los=aos + timedelta(minutes=10),
            max_elevation_deg=40.0,
        )

    def heard(at: datetime) -> ArchiveReception:
        return ArchiveReception(
            satellite_key="norad:25544",
            satellite_key_kind="norad",
            started_at=at + timedelta(minutes=3),
            archive_outcome="decoded",
            archive_station_id=7,
        )

    early, late = noon - timedelta(hours=2), noon + timedelta(hours=2)
    rows = SnapshotRows(
        passes=(),
        assignments=(),
        observations=(),
        heartbeats=(),
        listening={},
        archive=(heard(early), heard(late)),
        archive_passes=(computed(early), computed(late)),
    )

    selection = select((), rows, LabelConfig(), since=noon)

    (day,) = [one for one in selection.station_days if one.population == "archive"]
    assert (day.eligible, day.attempted) == (1, 1)
    assert selection.unmatched_receptions == 0
    assert len(selection.scored) == 1
