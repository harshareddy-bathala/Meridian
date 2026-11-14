"""The station planner: which passes, from where, scored by which model (D-370).

Three parts, each tested without a database:

* **The search.** :func:`search_windows` asks the orbit service the right
  question: the rounded place at sea level, from twenty minutes before the
  hour to 49 hours after it, against the 10° floor, and names a satellite SGP4
  cannot place.
* **The answer.** :func:`build_plan` keeps the passes under way at the minute
  or rising in the 48 hours after it, publishes them as every pass is
  (D-093), and gives each a yield or a stated absence (D-086).
* **The model.** ``meridian model planner`` writes ``plan-model-1`` from a
  verified model directory, and :func:`read_plan_model` takes back only a
  model that scores a pass from its peak alone.

The orbit is a stub: what the planner does with windows is the subject here,
and ``test_pass_windows_reference.py`` proves the windows.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from meridian.api.public import plan
from meridian.api.public.models.plan import PlanYield
from meridian.api.public.models.reliability import NotMeasuredFigure
from meridian.api.public.plan_model import (
    PLAN_FEATURE_VERSION,
    PLAN_FEATURES,
    PLAN_SCHEMA,
    HeldPlanModel,
    PlanModelRefusedError,
    read_plan_model,
)
from meridian.api.public.plan_search import (
    FLOOR_DEG,
    MAX_SATELLITES,
    NO_MODEL_REASON,
    Candidate,
    build_plan,
    round_place,
    search_windows,
)
from meridian.cli import main
from meridian.datasets.canonical import canonical_bytes
from meridian.datasets.manifest import Manifest, content_sha256, file_entry
from meridian.datasets.publish import publish_directory
from meridian.orbit.types import ElementSet, PassSearch, PassWindow, SubPoint
from meridian.prediction.features import FEATURE_VERSION
from meridian.prediction.model_files import MODEL_FILE, MODEL_VERSION
from meridian.prediction.planner_file import PLANNER_FEATURES, PLANNER_SCHEMA
from meridian.prediction.score import predict
from meridian.store.element_sets import StoredElementSet
from meridian.store.satellite_catalogue import CataloguedSatellite
from meridian.store.satellites import StoredTransmitter

AT = datetime(2026, 11, 14, 9, 37, tzinfo=UTC)
HOUR = datetime(2026, 11, 14, 9, 0, tzinfo=UTC)
EPOCH = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
PLACE = (12.3, 77.6)


def satellite(n: int) -> CataloguedSatellite:
    return CataloguedSatellite(
        satellite_id=f"norad:{57000 + n}",
        name=f"Meteor-{n}",
        orbital_regime="leo",
        priority=1.0,
        is_active=True,
        latest_element_set_epoch=EPOCH,
    )


def stored_set(n: int) -> StoredElementSet:
    return StoredElementSet(
        id=100 + n,
        satellite_id=f"norad:{57000 + n}",
        epoch=EPOCH,
        retrieved_at=EPOCH,
        line1="1 line",
        line2="2 line",
        source="celestrak",
        content_sha256=b"\x00" * 32,
    )


def candidate(n: int, *, element_set: bool = True) -> Candidate:
    return Candidate(
        satellite=satellite(n),
        downlinks=(StoredTransmitter(f"norad:{57000 + n}", 137_100_000, "lrpt"),),
        element_set=stored_set(n) if element_set else None,
    )


def window(
    n: int, aos: datetime, minutes: float = 12.0, peak: float = 41.6
) -> PassWindow:
    los = aos + timedelta(minutes=minutes)
    return PassWindow(
        satellite_id=f"norad:{57000 + n}",
        aos=aos,
        los=los,
        max_elevation_deg=peak,
        max_elevation_at=aos + (los - aos) / 2,
        aos_azimuth_deg=12.4,
        los_azimuth_deg=187.6,
        element_set_epoch=EPOCH,
        element_set_age_s=(aos - EPOCH).total_seconds(),
        min_elevation_deg=FLOOR_DEG,
    )


@dataclass
class StubOrbit:
    """Windows by satellite, and a record of every search."""

    windows: dict[str, list[PassWindow]] = field(default_factory=dict)
    unplaceable: frozenset[str] = frozenset()
    too_high: frozenset[str] = frozenset()
    searches: list[PassSearch] = field(default_factory=list)

    def pass_windows(self, search: PassSearch) -> list[PassWindow]:
        self.searches.append(search)
        if search.element_set.satellite_id in self.too_high:
            raise ValueError("a pass outlasts the search margin")
        return self.windows.get(search.element_set.satellite_id, [])

    def ground_track(
        self, element_set: ElementSet, start: datetime, end: datetime, *, step_s: float
    ) -> list[SubPoint]:
        del end, step_s
        lat = float("nan") if element_set.satellite_id in self.unplaceable else 10.0
        return [SubPoint(t=start, lat_deg=lat, lon_deg=20.0)]


def a_model_document(**changes: object) -> dict[str, object]:
    document: dict[str, object] = {
        "model_format": 1,
        "feature_version": FEATURE_VERSION,
        "configuration": "A",
        "population": "own",
        "reads_history": False,
        "min_station_history": 5,
        "configured": {
            "features": ["max_elevation_deg"],
            "mean": [35.0],
            "scale": [18.0],
            "coefficients": [1.2],
            "intercept": -0.3,
            "calibration": {"method": "platt", "a": 0.9, "b": 0.1},
        },
        "fallback": None,
    }
    return document | changes


def a_plan_file(path: Path, **changes: object) -> Path:
    body: dict[str, object] = {
        "schema": "plan-model-1",
        "model_sha256": "ab" * 32,
        "configuration": "A",
        "population": "own",
        "as_of": "2026-11-01T00:00:00+00:00",
        "dataset_sha256": "cd" * 32,
        "fitted": a_model_document(),
    }
    path.write_text(json.dumps(body | changes), encoding="utf-8")
    return path


# --- the place --------------------------------------------------------------


def test_a_place_is_used_to_a_tenth_of_a_degree() -> None:
    assert round_place(12.345678, 77.654321) == (12.3, 77.7)


def test_a_place_rounding_to_zero_is_never_negative_zero() -> None:
    lat, lon = round_place(-0.04, -0.01)
    assert (str(lat), str(lon)) == ("0.0", "0.0")


# --- the search -------------------------------------------------------------


def test_the_search_covers_every_minute_of_the_hour_against_the_floor() -> None:
    orbit = StubOrbit()
    search_windows(orbit, [candidate(1)], PLACE, HOUR)

    (search,) = orbit.searches
    assert (search.site.lat_deg, search.site.lon_deg, search.site.alt_m) == (
        12.3,
        77.6,
        0,
    )
    assert search.start == HOUR - timedelta(minutes=20)
    assert search.end == HOUR + timedelta(hours=49)
    assert search.min_elevation_deg == FLOOR_DEG == 10.0


def test_a_satellite_sgp4_cannot_place_or_that_never_sets_is_named() -> None:
    orbit = StubOrbit(
        unplaceable=frozenset({"norad:57001"}), too_high=frozenset({"norad:57002"})
    )
    found = search_windows(
        orbit, [candidate(1), candidate(2), candidate(3)], PLACE, HOUR
    )

    assert found == {"norad:57001": None, "norad:57002": None, "norad:57003": []}


def test_a_satellite_without_an_element_set_is_not_searched() -> None:
    orbit = StubOrbit()
    assert search_windows(orbit, [candidate(1, element_set=False)], PLACE, HOUR) == {}
    assert orbit.searches == []


# --- the answer -------------------------------------------------------------


def test_the_pass_under_way_and_those_rising_in_48_hours_are_listed() -> None:
    under_way = window(1, AT - timedelta(minutes=5))
    over = window(1, AT - timedelta(minutes=30))
    last = window(1, AT + timedelta(hours=47, minutes=59))
    beyond = window(1, AT + timedelta(hours=48))
    windows = {"norad:57001": [over, under_way, last, beyond]}

    body = build_plan([candidate(1)], windows, PLACE, AT, None)

    assert body.at == AT
    assert body.until == AT + timedelta(hours=48)
    assert [one.aos for one in body.passes] == [
        datetime(2026, 11, 14, 9, 32, tzinfo=UTC),
        datetime(2026, 11, 16, 9, 36, tzinfo=UTC),
    ]


def test_a_pass_is_published_as_every_pass_is() -> None:
    """Widened to whole minutes, its culmination floored, angles whole (D-093)."""
    one = window(1, AT + timedelta(minutes=3, seconds=41), minutes=11.4, peak=41.6)
    body = build_plan([candidate(1)], {"norad:57001": [one]}, PLACE, AT, None)

    (published,) = body.passes
    assert published.aos == datetime(2026, 11, 14, 9, 40, tzinfo=UTC)
    assert published.los == datetime(2026, 11, 14, 9, 53, tzinfo=UTC)
    assert published.max_elevation_at == datetime(2026, 11, 14, 9, 46, tzinfo=UTC)
    assert (published.max_elevation_deg, published.aos_azimuth_deg) == (42, 12)
    assert published.los_azimuth_deg == 188


def test_passes_from_every_satellite_are_in_order_of_acquisition() -> None:
    windows = {
        "norad:57001": [window(1, AT + timedelta(hours=2))],
        "norad:57002": [window(2, AT + timedelta(hours=1))],
    }
    body = build_plan([candidate(1), candidate(2)], windows, PLACE, AT, None)

    assert [one.satellite_id for one in body.passes] == ["norad:57002", "norad:57001"]
    assert [one.satellite_id for one in body.satellites] == [
        "norad:57001",
        "norad:57002",
    ]


def test_without_a_model_every_yield_is_a_stated_absence() -> None:
    """D-086: never a null, never a zero, where a chart would read a number."""
    windows = {"norad:57001": [window(1, AT + timedelta(hours=1))]}
    body = build_plan([candidate(1)], windows, PLACE, AT, None)

    assert body.yield_model.status == "not_yet_computed"
    assert body.passes[0].predicted_yield == NotMeasuredFigure(reason=NO_MODEL_REASON)
    assert "probability" not in body.model_dump_json()


def test_with_a_model_a_yield_is_configuration_as_score_from_the_real_peak(
    tmp_path: Path,
) -> None:
    held = read_plan_model(a_plan_file(tmp_path / "plan-model.json"))
    windows = {"norad:57001": [window(1, AT + timedelta(hours=1), peak=41.6)]}
    body = build_plan([candidate(1)], windows, PLACE, AT, held)

    expected = predict(held.model, {"max_elevation_deg": 41.6}, station_history=0)
    assert body.passes[0].predicted_yield == PlanYield(
        status="computed", probability=round(expected.probability, 2)
    )
    model = body.yield_model
    assert model.status == "computed"
    assert (model.configuration, model.population, model.model_sha256) == (
        "A",
        "own",
        "ab" * 32,
    )


def test_each_satellite_says_what_to_tune_to_and_how_old_its_orbit_is() -> None:
    body = build_plan([candidate(1)], {"norad:57001": []}, PLACE, AT, None)

    (one,) = body.satellites
    assert [(d.centre_freq_hz, d.mode) for d in one.downlinks] == [
        (137_100_000, "lrpt")
    ]
    assert one.element_set_age_s == (AT - EPOCH).total_seconds()
    assert one.element_set_source == "celestrak"


def test_every_satellite_not_searched_is_named_with_its_reason() -> None:
    candidates = [
        candidate(1, element_set=False),
        candidate(2),
        *(candidate(n) for n in range(3, MAX_SATELLITES + 3)),
    ]
    windows: dict[str, Sequence[PassWindow] | None] = {
        one.satellite.satellite_id: [] for one in candidates
    }
    windows["norad:57002"] = None

    body = build_plan(candidates, windows, PLACE, AT, None)

    assert [(one.satellite_id, one.reason) for one in body.omitted] == [
        ("norad:57001", "no_element_set"),
        ("norad:57002", "propagation_failed"),
        (f"norad:{57000 + MAX_SATELLITES + 1}", "over_limit"),
    ]
    assert len(body.satellites) == MAX_SATELLITES - 2


def test_the_place_is_echoed_rounded_at_sea_level() -> None:
    body = build_plan([], {}, PLACE, AT, None)
    assert body.place.model_dump() == {"lat_deg": 12.3, "lon_deg": 77.6, "alt_m": 0}
    assert body.min_elevation_deg == 10


# --- the window cache -------------------------------------------------------


def test_one_place_is_searched_once_an_hour(monkeypatch: pytest.MonkeyPatch) -> None:
    orbit = StubOrbit()
    cache = plan._WindowCache()
    monkeypatch.setattr(cache, "_orbit", orbit)

    cache.windows([candidate(1)], PLACE, HOUR)
    cache.windows([candidate(1)], PLACE, HOUR)
    cache.windows([candidate(1)], (12.4, 77.6), HOUR)
    cache.windows([candidate(1)], PLACE, HOUR + timedelta(hours=1))

    assert len(orbit.searches) == 3


def test_a_new_element_set_is_searched_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    orbit = StubOrbit()
    cache = plan._WindowCache()
    monkeypatch.setattr(cache, "_orbit", orbit)
    newer = Candidate(
        satellite=satellite(1),
        downlinks=candidate(1).downlinks,
        element_set=StoredElementSet(
            id=999,
            satellite_id="norad:57001",
            epoch=EPOCH + timedelta(hours=6),
            retrieved_at=EPOCH,
            line1="1 line",
            line2="2 line",
            source="celestrak",
            content_sha256=b"\x01" * 32,
        ),
    )

    cache.windows([candidate(1)], PLACE, HOUR)
    cache.windows([newer], PLACE, HOUR)

    assert len(orbit.searches) == 2


# --- the model file ---------------------------------------------------------


def test_the_version_and_schema_written_twice_are_the_same() -> None:
    """D-061's rule: a value the API cannot import is held equal by a test."""
    assert PLAN_FEATURE_VERSION == FEATURE_VERSION
    assert PLANNER_SCHEMA == PLAN_SCHEMA
    assert PLANNER_FEATURES == PLAN_FEATURES


@pytest.mark.parametrize(
    ("changes", "match"),
    [
        ({"schema": "plan-model-2"}, "not plan-model-1"),
        ({"surprise": 1}, "not plan-model-1"),
        ({"configuration": "D"}, "not plan-model-1"),
        ({"model_sha256": "AB" * 32}, "not plan-model-1"),
        ({"fitted": {"model_format": 9}}, "cannot be scored"),
        ({"fitted": a_model_document(configuration="C")}, "configuration C"),
        (
            {
                "fitted": a_model_document(
                    configured={
                        "features": ["max_elevation_deg", "duration_min"],
                        "mean": [35.0, 10.0],
                        "scale": [18.0, 3.0],
                        "coefficients": [1.2, 0.1],
                        "intercept": -0.3,
                        "calibration": {"a": 0.9, "b": 0.1},
                    }
                )
            },
            "alone",
        ),
        ({"fitted": a_model_document(feature_version="features-0")}, "fit it again"),
    ],
)
def test_a_file_the_planner_cannot_score_from_a_peak_is_refused(
    tmp_path: Path, changes: dict[str, object], match: str
) -> None:
    with pytest.raises(PlanModelRefusedError, match=match):
        read_plan_model(a_plan_file(tmp_path / "plan-model.json", **changes))


def a_model_directory(root: Path, document: dict[str, object]) -> Path:
    """A model directory as ``publish_model`` writes one, verified on reading."""
    data = canonical_bytes(document | {"dataset_sha256": "cd" * 32}) + b"\n"
    manifest = Manifest(
        kind="model",
        schema_revision="0032",
        since=datetime(2026, 9, 1, tzinfo=UTC),
        as_of=datetime(2026, 11, 1, tzinfo=UTC),
        files=(file_entry(MODEL_FILE, data),),
        created_at=datetime(2026, 11, 2, tzinfo=UTC),
        derived_from=bytes.fromhex("cd" * 32),
        transformation_version=MODEL_VERSION,
        config_sha256=bytes.fromhex("ef" * 32),
    )
    name = content_sha256(manifest).hex()[:12]
    return publish_directory(root, name, manifest, {MODEL_FILE: data}).path


def test_the_planner_command_writes_a_file_the_api_takes_back(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = a_model_directory(tmp_path / "models", a_model_document())
    output = tmp_path / "published" / "plan-model.json"

    assert main(["model", "planner", str(directory), "--output", str(output)]) == 0

    held = read_plan_model(output)
    assert isinstance(held, HeldPlanModel)
    assert held.file.population == "own"
    assert held.file.as_of == datetime(2026, 11, 1, tzinfo=UTC)
    assert held.file.dataset_sha256 == "cd" * 32
    assert held.file.model_sha256 in capsys.readouterr().out


def test_the_planner_command_refuses_a_model_that_reads_more_than_a_peak(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    document = a_model_document(
        configuration="D",
        reads_history=True,
        fallback=a_model_document()["configured"],
    )
    directory = a_model_directory(tmp_path / "models", document)
    output = tmp_path / "plan-model.json"

    assert main(["model", "planner", str(directory), "--output", str(output)]) == 1
    assert "configuration D" in capsys.readouterr().err
    assert not output.exists()
