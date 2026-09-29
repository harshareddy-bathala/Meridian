"""The pre-pass rule, one clause at a time, on values written by hand.

Reference: docs/DECISIONS.md D-221, D-222, D-224.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

from meridian.datasets.canonical import canonical_line
from meridian.datasets.environment_rows import read_environment_samples
from meridian.prediction.conditions import CLOUD, KP, Conditions, value_before

T = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
HERE = (12.97, 77.59)


def sample(  # noqa: PLR0913, PLR0917 — a row is this wide
    n: int,
    observed: datetime,
    published: datetime,
    value: float | None = 2.0,
    quantity: str = "kp_index",
    place: tuple[float, float] | None = None,
    hours: int = 3,
    record: int = 1,
) -> dict[str, object]:
    return {
        "sample_id": n,
        "record_id": record,
        "source_id": "noaa_swpc_kp",
        "series_key": f"{quantity}:{observed.isoformat()}@{place}",
        "quantity": quantity,
        "value": value,
        "missing_reason": None if value is not None else "published without a value",
        "value_unit": "u",
        "observed_from": observed,
        "observed_to": observed + timedelta(hours=hours),
        "published_at": published,
        "product": "p",
        "lat_deg": None if place is None else place[0],
        "lon_deg": None if place is None else place[1],
        "footprint_m": None,
        "quality": None,
    }


def read(*rows: dict[str, object]) -> tuple[object, ...]:
    data = b"".join(canonical_line(one) for one in rows)
    return read_environment_samples({"environment_samples.jsonl": data})


def test_a_value_published_after_the_pass_is_never_read() -> None:
    samples = read(sample(1, T - timedelta(hours=3), T + timedelta(minutes=1)))
    choice = value_before(samples, KP, T, None)  # type: ignore[arg-type]
    assert choice.value is None
    assert choice.reason == "nothing published before the pass"


def test_publication_at_the_instant_of_the_pass_is_not_before_it() -> None:
    samples = read(sample(1, T - timedelta(hours=3), T))
    assert value_before(samples, KP, T, None).value is None  # type: ignore[arg-type]


def test_the_latest_interval_begun_by_the_pass_wins() -> None:
    samples = read(
        sample(1, T - timedelta(hours=6), T - timedelta(hours=1), value=1.0),
        sample(2, T - timedelta(hours=3), T - timedelta(hours=1), value=3.0),
        sample(3, T + timedelta(hours=1), T - timedelta(hours=1), value=9.0),
    )
    assert value_before(samples, KP, T, None).value == 3.0  # type: ignore[arg-type]


def test_a_published_gap_is_missing_and_never_the_older_value() -> None:
    """D-221: falling back across a published gap would invent a reading."""
    samples = read(
        sample(1, T - timedelta(hours=6), T - timedelta(hours=1), value=1.0),
        sample(2, T - timedelta(hours=3), T - timedelta(hours=1), value=None),
    )
    choice = value_before(samples, KP, T, None)  # type: ignore[arg-type]
    assert choice.value is None
    assert choice.reason == "published as missing: published without a value"


def test_a_value_too_old_to_describe_the_pass_is_missing() -> None:
    samples = read(sample(1, T - timedelta(hours=12), T - timedelta(hours=8)))
    choice = value_before(samples, KP, T, None)  # type: ignore[arg-type]
    assert choice.reason == "the latest value is too old"


def test_cloud_is_the_forecast_for_the_pass_hour_at_the_nearest_cell() -> None:
    samples = read(
        sample(1, T, T - timedelta(hours=2), 80.0, "cloud_cover", (12.98, 77.6), 1),
        sample(2, T, T - timedelta(hours=2), 10.0, "cloud_cover", (13.1, 77.7), 1),
        sample(3, T, T - timedelta(hours=2), 55.0, "cloud_cover", (28.6, 77.2), 1),
    )
    assert value_before(samples, CLOUD, T + timedelta(minutes=20), HERE).value == 80.0  # type: ignore[arg-type]


def test_cloud_far_from_the_station_or_not_covering_the_pass_is_missing() -> None:
    far = read(
        sample(1, T, T - timedelta(hours=2), 55.0, "cloud_cover", (28.6, 77.2), 1)
    )
    assert value_before(far, CLOUD, T, HERE).value is None  # type: ignore[arg-type]
    earlier = read(
        sample(
            1,
            T - timedelta(hours=3),
            T - timedelta(hours=4),
            30.0,
            "cloud_cover",
            HERE,
            1,
        )
    )
    choice = value_before(earlier, CLOUD, T, HERE)  # type: ignore[arg-type]
    assert choice.reason == "nothing published covers the pass"


def test_a_station_with_no_known_position_reads_no_local_value() -> None:
    samples = read(sample(1, T, T - timedelta(hours=2), 40.0, "cloud_cover", HERE, 1))
    assert value_before(samples, CLOUD, T, None).value is None  # type: ignore[arg-type]


def test_missing_is_encoded_as_zero_beside_an_indicator_of_zero() -> None:
    conditions = Conditions(
        read(sample(1, T - timedelta(hours=3), T - timedelta(hours=1), value=4.33)),  # type: ignore[arg-type]
        places={"st": HERE},
    )
    assert conditions.values("st", T) == (4.33, 1.0, 0.0, 0.0)


def test_a_value_normalised_twice_is_read_once_from_the_later_load() -> None:
    """D-140 appends a re-normalisation; the reader keeps the last loaded."""
    first = sample(1, T, T - timedelta(hours=1), value=1.0)
    again = {**sample(7, T, T - timedelta(hours=1), value=1.5)}
    samples = read(first, again)
    assert [(one.sample_id, one.value) for one in samples] == [(7, 1.5)]  # type: ignore[attr-defined]


def test_a_snapshot_from_before_stage_31_holds_no_values() -> None:
    assert read_environment_samples({}) == ()


def test_the_indexed_answer_is_the_scanned_answer_for_every_pass() -> None:
    """``Conditions`` searches an index; ``value_before`` is the definition.

    Seeded rows at three places — one near, one beyond 25 km, one global —
    with forecasts republished hourly and revisions published after later
    passes, then every pass across two days asked of both.
    """
    generator = random.Random(31)
    near, far = (12.99, 77.61), (13.40, 77.59)
    rows = []
    for n in range(600):
        quantity, place, hours = generator.choice(
            [("kp_index", None, 3), ("cloud_cover", near, 1), ("cloud_cover", far, 1)]
        )
        observed = T + timedelta(hours=generator.randrange(-24, 24))
        published = observed + timedelta(hours=generator.randrange(-30, 6))
        value = None if generator.random() < 0.1 else generator.uniform(0, 9)
        rows.append(sample(n, observed, published, value, quantity, place, hours))
    samples = read(*rows)
    conditions = Conditions(samples, {"st": HERE})  # type: ignore[arg-type]

    for minutes in range(-24 * 60, 24 * 60, 17):
        at = T + timedelta(minutes=minutes)
        assert conditions.choices("st", at) == (
            value_before(samples, KP, at, None),  # type: ignore[arg-type]
            value_before(samples, CLOUD, at, HERE),  # type: ignore[arg-type]
        )
