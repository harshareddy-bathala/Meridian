"""The evidence package's rows, from a raw snapshot alone.

Marked as a unit test by living in ``tests/unit``. What a row holds, and what
a published package must never carry:
- one row per reception a station held, a miss with no report included, and
  no decline, revoked work, skipped pass, unacknowledged work or open window;
- the current observation, and its lineage oldest first;
- every verdict on the current revision, and every diagnosis;
- products by hash and size, of the current revision, and never a URI;
- no station coordinates or name, no station notes, no element-set lines;
- simulated receptions only when asked for, and apart.

Reference: docs/DECISIONS.md D-008, D-104, D-134, D-145, D-176, D-310, D-311.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from meridian.datasets.canonical import canonical_line
from meridian.datasets.evidence_rows import reception_rows
from meridian.datasets.publish import read_directory


@pytest.fixture
def rows(
    evidence_snapshot: Callable[[Any], Path], evidence_world: Any
) -> dict[str, list[dict[str, object]]]:
    raw = read_directory(evidence_snapshot(evidence_world))
    return reception_rows(raw, ["measured", "simulated"])


def by_id(found: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    return {str(one["assignment_id"]): one for one in found}


def test_one_row_per_reception_a_station_held(
    rows: dict[str, list[dict[str, object]]],
) -> None:
    assert [one["assignment_id"] for one in rows["measured"]] == ["as_1", "as_2"]
    assert [one["assignment_id"] for one in rows["simulated"]] == ["as_6"]


def test_the_current_observation_and_its_lineage_oldest_first(
    rows: dict[str, list[dict[str, object]]],
) -> None:
    decoded = by_id(rows["measured"])["as_1"]
    observation = decoded["observation"]

    assert isinstance(observation, dict)
    assert (observation["revision"], observation["outcome"]) == (2, "decoded")
    assert [one["revision"] for one in decoded["lineage"]] == [1, 2]  # type: ignore[attr-defined]
    assert decoded["listening_confirmed"] is True


def test_a_miss_with_no_report_keeps_its_diagnosis(
    rows: dict[str, list[dict[str, object]]],
) -> None:
    miss = by_id(rows["measured"])["as_2"]

    assert miss["observation"] is None
    assert miss["lineage"] == []
    assert miss["listening_confirmed"] is False
    [diagnosis] = miss["diagnoses"]  # type: ignore[misc]
    assert diagnosis["cause"] == "station_not_listening"
    assert diagnosis["method"] == "diagnosis-1"


def test_every_verdict_on_the_current_revision_by_method(
    rows: dict[str, list[dict[str, object]]],
) -> None:
    verdicts = by_id(rows["measured"])["as_1"]["verdicts"]

    assert [(one["method"], one["probability_usable"]) for one in verdicts] == [  # type: ignore[attr-defined]
        ("verdict-1:aaaaaaaaaaaa", 0.81),
        ("verdict-2:bbbbbbbbbbbb", 0.77),
    ]


def test_products_are_referenced_by_hash_and_never_by_uri(
    rows: dict[str, list[dict[str, object]]],
) -> None:
    products = by_id(rows["measured"])["as_1"]["products"]

    assert [one["kind"] for one in products] == ["image", "frames"]  # type: ignore[attr-defined]
    assert all(set(one) == {"kind", "sha256", "size_bytes"} for one in products)  # type: ignore[attr-defined]


def test_nothing_private_or_ingested_is_carried(
    rows: dict[str, list[dict[str, object]]], evidence_world: Any
) -> None:
    written = b"".join(
        canonical_line(one) for found in rows.values() for one in found
    ).decode("utf-8")
    station = evidence_world["stations"][0]
    element_set = evidence_world["element_sets"][0]

    for private in (
        str(station["lat_deg"]),
        str(station["lon_deg"]),
        station["name"],
        "private note from the station",
        "file:///private/path.png",
        element_set["line1"],
        element_set["line2"],
    ):
        assert private not in written, private
    row = by_id(rows["measured"])["as_1"]
    stored = json.loads(canonical_line(element_set))
    assert row["element_set"] == {
        "id": 1,
        "epoch": stored["epoch"],
        "source": "celestrak",
        "content_sha256": stored["content_sha256"],
    }


def test_simulated_receptions_are_absent_unless_named(
    evidence_snapshot: Callable[[Any], Path], evidence_world: Any
) -> None:
    raw = read_directory(evidence_snapshot(evidence_world))

    measured = reception_rows(raw, ["measured"])

    assert set(measured) == {"measured"}
    assert all(one["simulated"] is False for one in measured["measured"])
    assert json.dumps(measured, default=str).count("st_sim") == 0
