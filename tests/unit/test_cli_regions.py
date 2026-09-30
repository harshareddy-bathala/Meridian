"""``meridian regions report`` at a prompt: no database, and the same directory twice.

Reference: docs/DECISIONS.md D-227, D-229.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from meridian import cli
from meridian.regions.geometry import Polygon

AREA = Polygon.from_bbox(77.45, 12.85, 77.75, 13.10)


@pytest.fixture
def snapshot(raw_snapshot: Any) -> Path:
    return raw_snapshot(
        {
            "areas_of_interest": [
                {
                    "area_id": 1,
                    "label": "Bengaluru urban",
                    "geometry": AREA.to_geojson(),
                    "area_km2": AREA.area_km2(),
                    "active": True,
                }
            ]
        }
    )


def test_a_report_runs_twice_from_a_snapshot_and_names_one_directory(
    snapshot: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ["regions", "report", "--snapshot", str(snapshot), "--root", str(tmp_path)]
    assert cli.main(argv) == 0
    first = capsys.readouterr().out
    assert cli.main(argv) == 0
    second = capsys.readouterr().out

    assert "written" in first.splitlines()[0]
    assert "already held, identically" in second.splitlines()[0]
    assert first.splitlines()[0].split()[0] == second.splitlines()[0].split()[0]
    assert "area 1  Bengaluru urban" in second


def test_a_report_from_something_that_is_not_a_snapshot_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["regions", "report", "--snapshot", str(tmp_path)]) == 1
    assert "meridian regions report:" in capsys.readouterr().err


def test_an_area_describing_a_person_is_refused_before_the_database(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody@127.0.0.1:1/none")
    code = cli.main(
        ["regions", "add", "--label", "someone@example.org", "--bbox", "0,0,1,1"]
    )
    assert code == 1
    assert "never a person" in capsys.readouterr().err
