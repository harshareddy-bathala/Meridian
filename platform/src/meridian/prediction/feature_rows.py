"""The raw snapshot's rows a feature reads, typed once.

:mod:`meridian.datasets.snapshot_rows` reads only what a label depends on, on
purpose. A feature depends on more — azimuths, the element set's epoch, the
pass's frozen track (D-158), the satellite's band — and those are read here,
by the same field helpers, so a malformed row is refused the same way.

Every row is keyed by the prediction it describes. A labelled physical pass
names its representative prediction (D-148), and its geometry is that
prediction's.

Reference: docs/DECISIONS.md D-148, D-157, D-158.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from meridian.datasets.row_fields import (
    MalformedSnapshotError,
    instant,
    integer,
    jsonl_rows,
    number,
    text,
)

__all__ = [
    "FeatureRows",
    "PassGeometry",
    "PassTrack",
    "band_of",
    "read_feature_rows",
]

_BANDS = (
    (300e6, "vhf"),
    (1e9, "uhf"),
    (2e9, "l"),
    (4e9, "s"),
    (8e9, "c"),
    (12e9, "x"),
)
"""Upper edges, in hertz, of the bands a history is kept per (ITU letters)."""


@dataclass(frozen=True, slots=True)
class PassTrack:
    """Azimuth and elevation every ``step_s`` from ``start``, as export froze them."""

    start: datetime
    step_s: int
    azimuth_deg: tuple[float, ...]
    elevation_deg: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class PassGeometry:
    """One prediction's geometry, all of it known before the pass."""

    max_elevation_deg: float
    aos_azimuth_deg: float
    los_azimuth_deg: float
    element_set_epoch: datetime
    track: PassTrack | None
    """``None`` where the export could not compute one, and counted it."""


@dataclass(frozen=True, slots=True)
class FeatureRows:
    """What features read from a raw snapshot, beyond the labels."""

    geometry: Mapping[int, PassGeometry]
    """By prediction (``passes.id``)."""

    bands: Mapping[str, str]
    """Each satellite's band, from its lowest-numbered transmitter."""


def read_feature_rows(files: Mapping[str, bytes]) -> FeatureRows:
    """Read the geometry and bands out of a raw snapshot.

    Args:
        files: The raw snapshot's files, by name, as read and verified.

    Returns:
        The typed rows.

    Raises:
        MalformedSnapshotError: A file is missing — ``pass_tracks.jsonl`` from
            a snapshot exported before Stage 17 — or a row is the wrong shape.
    """
    epochs = {
        integer(one, "id"): instant(one, "epoch")
        for one in _lines(files, "element_sets")
    }
    tracks = {
        integer(one, "pass_id"): _track(one) for one in _lines(files, "pass_tracks")
    }
    geometry = {}
    for one in _lines(files, "passes"):
        pass_id = integer(one, "id")
        element_set_id = integer(one, "element_set_id")
        if element_set_id not in epochs:
            message = f"pass {pass_id} names element set {element_set_id}, not held"
            raise MalformedSnapshotError(message)
        geometry[pass_id] = PassGeometry(
            max_elevation_deg=number(one, "max_elevation_deg"),
            aos_azimuth_deg=number(one, "aos_azimuth_deg"),
            los_azimuth_deg=number(one, "los_azimuth_deg"),
            element_set_epoch=epochs[element_set_id],
            track=tracks.get(pass_id),
        )
    transmitters = sorted(
        _lines(files, "transmitters"), key=lambda one: integer(one, "id")
    )
    bands: dict[str, str] = {}
    for one in transmitters:
        bands.setdefault(
            text(one, "satellite_id"), band_of(number(one, "centre_freq_hz"))
        )
    return FeatureRows(geometry=geometry, bands=bands)


def band_of(frequency_hz: float) -> str:
    """The band a frequency is in, or ``"other"`` above 12 GHz."""
    for upper, name in _BANDS:
        if frequency_hz < upper:
            return name
    return "other"


def _track(row: Mapping[str, object]) -> PassTrack:
    azimuth, elevation = _numbers(row, "azimuth_deg"), _numbers(row, "elevation_deg")
    if len(azimuth) != len(elevation):
        message = f"pass {row.get('pass_id')!r}'s track has unequal series"
        raise MalformedSnapshotError(message)
    return PassTrack(
        start=instant(row, "start"),
        step_s=integer(row, "step_s"),
        azimuth_deg=azimuth,
        elevation_deg=elevation,
    )


def _numbers(row: Mapping[str, object], name: str) -> tuple[float, ...]:
    value = row.get(name)
    if not isinstance(value, list):
        message = f"{name} is {value!r}, not a list"
        raise MalformedSnapshotError(message)
    return tuple(number({name: one}, name) for one in value)


def _lines(files: Mapping[str, bytes], name: str) -> list[Mapping[str, object]]:
    try:
        data = files[f"{name}.jsonl"]
    except KeyError as exc:
        message = f"the raw snapshot has no {name}.jsonl"
        if name == "pass_tracks":
            message += (
                ": it was exported before Stage 17, which freezes each pass's"
                " track at export (D-158); export again to fit on it"
            )
        raise MalformedSnapshotError(message) from exc
    return jsonl_rows(data, f"{name}.jsonl")
