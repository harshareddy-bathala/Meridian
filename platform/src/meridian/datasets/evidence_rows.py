"""The evidence package's rows: one per reception, with what Meridian concluded.

A pure function of a raw snapshot (D-143): no database, no clock, no network.
Every value is copied from the snapshot's own rows, so a package never
recomputes a verdict or a diagnosis, which is what keeps its hash stable
(D-102).

**A reception is a pass a station held.** A scheduled assignment, neither
expired nor revoked, whose window had closed by the snapshot's ``as_of``, and
which the station either reported on or was still holding: a miss with no
report is a reception that returned nothing, and is in the package. An expired
assignment is a decline, never a reception (D-008), and is not.

**Each row carries:**

* the reception — its assignment, pass, window, frequency and mode, and whether
  the station was confirmed listening, as the registry answered at export
  (D-145);
* its current observation, or ``null`` when there was no report, and its
  lineage, every revision's content hash, oldest first;
* every verdict on the current revision, and every diagnosis, each with its
  method;
* the current revision's products, by ``sha256`` and size, never bundled;
* its provenance — station, satellite, the pass's geometry, and the element set
  by id, epoch, source and hash.

**Left out**, because a package is published (D-310): station coordinates,
which the snapshot holds at full precision; station names and the station's
own free-text notes; product URIs (D-176); and an element set's lines, which
are an ingested source's records, as are the archive and environment rows the
snapshot also holds (D-134).

Reference: docs/DECISIONS.md D-008, D-102, D-104, D-134, D-143, D-145, D-176,
D-310, D-311.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import datetime

from meridian.datasets.publish import SnapshotDirectory
from meridian.datasets.row_fields import (
    MalformedSnapshotError,
    field,
    flag,
    instant,
    integer,
    jsonl_rows,
    text,
)

__all__ = ["OBSERVATION_FIELDS", "reception_rows"]

Row = Mapping[str, object]

OBSERVATION_FIELDS = (
    "revision",
    "started_at",
    "ended_at",
    "outcome",
    "signal_detected",
    "first_detection_at",
    "peak_snr_db",
    "noise_floor_dbfs",
    "receiver_gain_db",
    "decoder",
    "decoder_version",
    "frames_decoded",
    "frames_failed",
    "snr_samples",
    "doppler_samples",
    "provenance",
    "submitted_at",
    "content_sha256",
)
"""What a package keeps of an observation. Not ``client_notes``, the station's
own words, nor ``products_json``, which holds each product's URI."""

_VERDICT_FIELDS = (
    "method",
    "probability_usable",
    "partial_below",
    "route",
    "inputs_sha256",
)
_DIAGNOSIS_FIELDS = (
    "diagnosis_id",
    "revision",
    "cause",
    "candidates_json",
    "evidence_json",
    "method",
    "config_sha256",
    "verdict_method",
    "classification_id",
)
_HELD_WITHOUT_A_REPORT = frozenset({"held", "in_progress"})
_NEVER_A_RECEPTION = frozenset({"expired", "revoked"})


def reception_rows(
    raw: SnapshotDirectory, populations: Iterable[str]
) -> dict[str, list[dict[str, object]]]:
    """Every reception of each population asked for, ordered by assignment.

    Args:
        raw: A raw snapshot, read and verified.
        populations: ``measured``, ``simulated`` or both.

    Returns:
        The rows of each population named, and of no other.

    Raises:
        MalformedSnapshotError: A row the package needs is missing or malformed.
    """
    wanted = {name: name == "simulated" for name in populations}
    tables = _Tables(raw)
    found: dict[str, list[dict[str, object]]] = {name: [] for name in wanted}
    for assignment in sorted(
        tables.assignments, key=lambda one: text(one, "assignment_id")
    ):
        simulated = flag(assignment, "simulated")
        population = "simulated" if simulated else "measured"
        if population in found and tables.is_reception(assignment):
            found[population].append(tables.row(assignment))
    return found


class _Tables:
    """The snapshot's rows, indexed by the keys a reception is joined on."""

    def __init__(self, raw: SnapshotDirectory) -> None:
        def rows(name: str) -> list[Row]:
            return jsonl_rows(raw.files.get(f"{name}.jsonl", b""), name)

        self.as_of: datetime = raw.manifest.as_of
        self.assignments = rows("assignments")
        self.passes = {integer(one, "id"): one for one in rows("passes")}
        self.element_sets = {integer(one, "id"): one for one in rows("element_sets")}
        self.listening = {
            text(one, "assignment_id"): flag(one, "listening_confirmed")
            for one in rows("listening")
        }
        self.revisions = _grouped(rows("observations"))
        self.verdicts = _grouped(rows("reception_verdicts"))
        self.diagnoses = _grouped(rows("loss_diagnoses"))
        self.products = _grouped(rows("products"))

    def is_reception(self, assignment: Row) -> bool:
        """Whether the station held this pass, and its window has closed."""
        if text(assignment, "decision") != "scheduled":
            return False
        if field(assignment, "revoked_reason") is not None:
            return False
        state = text(assignment, "state")
        if state in _NEVER_A_RECEPTION or instant(assignment, "end_at") > self.as_of:
            return False
        reported = bool(self.revisions.get(text(assignment, "assignment_id")))
        return reported or state in _HELD_WITHOUT_A_REPORT

    def row(self, assignment: Row) -> dict[str, object]:
        """The package's row for one reception."""
        key = text(assignment, "assignment_id")
        revisions = sorted(self.revisions.get(key, []), key=_revision_order)
        current = revisions[-1] if revisions else None
        revision = None if current is None else integer(current, "revision")
        pass_ = self._pass(assignment)
        return {
            "assignment_id": key,
            "pass_id": integer(assignment, "pass_id"),
            "station_id": text(assignment, "station_id"),
            "satellite_id": text(pass_, "satellite_id"),
            "window_start": field(assignment, "start_at"),
            "window_end": field(assignment, "end_at"),
            "centre_freq_hz": field(assignment, "centre_freq_hz"),
            "mode": field(assignment, "mode"),
            "listening_confirmed": self.listening.get(key),
            "pass": {
                name: field(pass_, name)
                for name in (
                    "aos",
                    "los",
                    "max_elevation_deg",
                    "aos_azimuth_deg",
                    "los_azimuth_deg",
                )
            },
            "element_set": self._element_set(pass_),
            "observation": None
            if current is None
            else _pick(current, OBSERVATION_FIELDS),
            "lineage": [
                {
                    "revision": integer(one, "revision"),
                    "content_sha256": field(one, "content_sha256"),
                    "submitted_at": field(one, "submitted_at"),
                }
                for one in revisions
            ],
            "verdicts": [
                _pick(one, _VERDICT_FIELDS)
                for one in sorted(
                    self.verdicts.get(key, []), key=lambda one: text(one, "method")
                )
                if revision is not None and integer(one, "revision") == revision
            ],
            "diagnoses": [
                _pick(one, _DIAGNOSIS_FIELDS)
                for one in sorted(
                    self.diagnoses.get(key, []),
                    key=lambda one: integer(one, "diagnosis_id"),
                )
            ],
            "products": [
                {
                    "kind": field(one, "kind"),
                    "sha256": field(one, "sha256"),
                    "size_bytes": field(one, "size_bytes"),
                }
                for one in sorted(
                    self.products.get(key, []),
                    key=lambda one: integer(one, "element_index"),
                )
                if revision is not None and integer(one, "revision") == revision
            ],
            "simulated": flag(assignment, "simulated"),
        }

    def _pass(self, assignment: Row) -> Row:
        pass_id = integer(assignment, "pass_id")
        if pass_id not in self.passes:
            raise MalformedSnapshotError(
                f"assignment's pass {pass_id} is not in the snapshot"
            )
        return self.passes[pass_id]

    def _element_set(self, pass_: Row) -> dict[str, object]:
        element_set_id = integer(pass_, "element_set_id")
        if element_set_id not in self.element_sets:
            message = f"pass's element set {element_set_id} is not in the snapshot"
            raise MalformedSnapshotError(message)
        held = self.element_sets[element_set_id]
        return {
            "id": element_set_id,
            "epoch": field(held, "epoch"),
            "source": field(held, "source"),
            "content_sha256": field(held, "content_sha256"),
        }


def _grouped(rows: list[Row]) -> dict[str, list[Row]]:
    grouped: dict[str, list[Row]] = defaultdict(list)
    for one in rows:
        grouped[text(one, "assignment_id")].append(one)
    return dict(grouped)


def _revision_order(row: Row) -> tuple[int, str]:
    return integer(row, "revision"), text(row, "started_at")


def _pick(row: Row, names: tuple[str, ...]) -> dict[str, object]:
    return {name: row.get(name) for name in names}
