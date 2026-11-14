"""The public cut of an evidence row: nothing that locates a station more finely.

D-310 keeps a station's coordinates out of every package. But a reception's
times and angles are computed from those coordinates, and D-093 found that, at
full precision, dozens of passes let anyone fit the station's position back
out of them. Its SNR and Doppler series are worse: a Doppler curve timed to the
millisecond is how a satellite fixes a transmitter's position. So the package
the public API serves is cut to D-093's rule (D-369):

* **every window is widened to whole minutes**, its start floored and its end
  ceiled, so it still contains the real one: the assignment's window, the
  pass's acquisition and loss of signal, and the observation's start and end;
* **every other instant is floored to the minute**: the first detection, and
  when each revision was submitted, which follows a pass's end by seconds;
* **every angle is whole degrees**: the peak elevation and both azimuths;
* **the SNR and Doppler series are left out**, as ``null``. The observation's
  peak SNR, noise floor, gain and frame counts stay;
* **each diagnosis keeps which causes were tested, which fired and with what
  support, but not what each test found**, which reads the station's sky
  sample by sample.

The verdicts, the outcome and every content hash are kept as they are. A hash
names the station's own report, and states nothing about where it was made.

A pure function of a row ``evidence_rows`` built. The full-precision package
stays with whoever holds the snapshot, and only they can regenerate it.

Reference: docs/DECISIONS.md D-082, D-093, D-310, D-369.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime

from meridian.datasets.row_fields import MalformedSnapshotError
from meridian.public_precision import ceil_minute, floor_minute, publish_angle

__all__ = ["public_row"]

_PASS_ANGLES = ("max_elevation_deg", "aos_azimuth_deg", "los_azimuth_deg")
_SERIES = ("snr_samples", "doppler_samples")
_CANDIDATE_KEPT = ("cause", "fired", "support")


def public_row(row: Mapping[str, object]) -> dict[str, object]:
    """One reception, cut to what the public API may state.

    Raises:
        MalformedSnapshotError: A time is not a timestamp, an angle not a
            number, or a part of the row is not the shape ``evidence_rows``
            writes.
    """
    cut = dict(row)
    cut["window_start"] = _instant(row, "window_start", floor_minute)
    cut["window_end"] = _instant(row, "window_end", ceil_minute)
    cut["pass"] = _pass(_part(row, "pass"))
    observation = row.get("observation")
    cut["observation"] = (
        None if observation is None else _observation(_part(row, "observation"))
    )
    cut["lineage"] = [
        {**one, "submitted_at": _instant(one, "submitted_at", floor_minute)}
        for one in _parts(row, "lineage")
    ]
    cut["diagnoses"] = [_diagnosis(one) for one in _parts(row, "diagnoses")]
    return cut


def _pass(held: Mapping[str, object]) -> dict[str, object]:
    cut = dict(held)
    cut["aos"] = _instant(held, "aos", floor_minute)
    cut["los"] = _instant(held, "los", ceil_minute)
    for name in _PASS_ANGLES:
        cut[name] = _angle(held, name)
    return cut


def _observation(held: Mapping[str, object]) -> dict[str, object]:
    cut = dict(held)
    cut["started_at"] = _instant(held, "started_at", floor_minute)
    cut["ended_at"] = _instant(held, "ended_at", ceil_minute)
    cut["first_detection_at"] = _instant(held, "first_detection_at", floor_minute)
    cut["submitted_at"] = _instant(held, "submitted_at", floor_minute)
    for name in _SERIES:
        cut[name] = None
    return cut


def _diagnosis(held: Mapping[str, object]) -> dict[str, object]:
    candidates = held.get("candidates_json")
    if not isinstance(candidates, list):
        raise MalformedSnapshotError("a diagnosis's candidates_json is not a list")
    return {
        **held,
        "candidates_json": [
            {name: _mapping(one, "candidate").get(name) for name in _CANDIDATE_KEPT}
            for one in candidates
        ],
    }


def _instant(
    held: Mapping[str, object], name: str, coarsen: Callable[[datetime], datetime]
) -> datetime | None:
    """A timestamp, coarsened; ``None`` stays ``None``."""
    value = held.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise MalformedSnapshotError(f"{name} is {value!r}, not a timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise MalformedSnapshotError(f"{name} is {value!r}, not a timestamp") from exc
    if parsed.tzinfo is None:
        raise MalformedSnapshotError(f"{name} is {value!r}, not a UTC timestamp")
    return coarsen(parsed)


def _angle(held: Mapping[str, object], name: str) -> int | None:
    value = held.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise MalformedSnapshotError(f"{name} is {value!r}, not an angle")
    return publish_angle(value)


def _part(row: Mapping[str, object], name: str) -> Mapping[str, object]:
    return _mapping(row.get(name), name)


def _parts(row: Mapping[str, object], name: str) -> list[Mapping[str, object]]:
    value = row.get(name)
    if not isinstance(value, list):
        raise MalformedSnapshotError(f"{name} is not a list")
    return [_mapping(one, name) for one in value]


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise MalformedSnapshotError(f"{name} is not an object")
    return value
