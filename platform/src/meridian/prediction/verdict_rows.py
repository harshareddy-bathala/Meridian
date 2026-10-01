"""Every observation revision in a raw snapshot, as the verdict's inputs.

The verdict is fitted and evaluated from snapshots, as every model here is
(D-143), so this reads the same inputs from the files that Stage 26's writer
reads from the database:
- the outcome, detection, peak SNR and decoder statistics from
  ``observations.jsonl``;
- the mode and frequency from the revision's assignment;
- frames expected from :func:`meridian.datasets.frames_expected.read_frames_expected`,
  the one definition (D-250);
- listening from ``listening.jsonl``, the registry's answers frozen at export
  (D-145). An assignment the registry was not asked about reads as not
  confirmed, which is what the writer's registry call answers for it too.

Every revision is read, measured and simulated alike, each carrying
``simulated`` from its row. Leaving the simulated ones out of fitting and
scoring is the fitter's refusal to make (D-078), and it counts them as it does.

Reference: docs/DECISIONS.md D-143, D-145, D-250, D-261.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from meridian.datasets.frames_expected import read_frames_expected
from meridian.datasets.row_fields import (
    MalformedSnapshotError,
    field,
    flag,
    instant,
    integer,
    jsonl_rows,
    optional_number,
    text,
)
from meridian.prediction.feature_rows import band_of
from meridian.prediction.verdict_inputs import ReceptionInputs

__all__ = ["Reception", "read_receptions"]


@dataclass(frozen=True, slots=True)
class Reception:
    """One observation revision: who, when, where in the spectrum, and its inputs."""

    assignment_id: str
    revision: int
    station_id: str
    satellite_id: str
    started_at: datetime
    band: str
    simulated: bool
    inputs: ReceptionInputs


def read_receptions(files: Mapping[str, bytes]) -> tuple[Reception, ...]:
    """Every observation revision's inputs, oldest first.

    Args:
        files: A raw snapshot's files, by name, as read and verified.

    Returns:
        One reception per row of ``observations.jsonl``, ordered by start, then
        assignment and revision.

    Raises:
        MalformedSnapshotError: A needed file is missing, a field is of the
            wrong type, or an observation names an assignment not held.
    """
    assignments = {
        text(one, "assignment_id"): (text(one, "mode"), integer(one, "centre_freq_hz"))
        for one in _lines(files, "assignments")
    }
    expected = read_frames_expected(files)
    listening = {
        text(one, "assignment_id"): flag(one, "listening_confirmed")
        for one in _lines(files, "listening")
    }
    receptions = []
    for one in _lines(files, "observations"):
        assignment_id = text(one, "assignment_id")
        if assignment_id not in assignments:
            message = f"observation of {assignment_id} names no assignment held"
            raise MalformedSnapshotError(message)
        mode, frequency = assignments[assignment_id]
        receptions.append(
            Reception(
                assignment_id=assignment_id,
                revision=integer(one, "revision"),
                station_id=text(one, "station_id"),
                satellite_id=text(one, "satellite_id"),
                started_at=instant(one, "started_at"),
                band=band_of(frequency),
                simulated=flag(one, "simulated"),
                inputs=ReceptionInputs(
                    outcome=text(one, "outcome"),
                    signal_detected=flag(one, "signal_detected"),
                    peak_snr_db=optional_number(one, "peak_snr_db"),
                    frames_decoded=_optional_integer(one, "frames_decoded"),
                    frames_expected=expected.get(assignment_id),
                    decoder=_optional_text(one, "decoder"),
                    decoder_version=_optional_text(one, "decoder_version"),
                    listening_confirmed=listening.get(assignment_id, False),
                    mode=mode,
                ),
            )
        )
    receptions.sort(key=lambda one: (one.started_at, one.assignment_id, one.revision))
    return tuple(receptions)


def _optional_integer(row: Mapping[str, object], name: str) -> int | None:
    return None if row.get(name) is None else integer(row, name)


def _optional_text(row: Mapping[str, object], name: str) -> str | None:
    value = field(row, name)
    if value is None:
        return None
    return text(row, name)


def _lines(files: Mapping[str, bytes], name: str) -> list[Mapping[str, object]]:
    """Every row of one file, parsed, or a refusal naming the missing file."""
    try:
        data = files[f"{name}.jsonl"]
    except KeyError as exc:
        message = f"the raw snapshot has no {name}.jsonl"
        raise MalformedSnapshotError(message) from exc
    return jsonl_rows(data, f"{name}.jsonl")
