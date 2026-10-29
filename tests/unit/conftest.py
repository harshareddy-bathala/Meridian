"""Fixtures the unit tests of Stage 15 share: a raw snapshot, built without a database.

A raw snapshot is files and a manifest, so the labelling side of the stage can
be tested end to end — publish, label, verify, the gate — with rows written by
hand. ``raw_snapshot`` publishes one exactly as the export would, through the
same writer, so what the labeller reads is what it will read in production.

Reference: docs/DECISIONS.md D-143, D-144.
"""

from __future__ import annotations

import math
import random
import socket
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest

from meridian.datasets.canonical import canonical_line
from meridian.datasets.manifest import Manifest, SourceEntry, content_sha256, file_entry
from meridian.datasets.publish import publish_directory

AS_OF = datetime(2026, 9, 23, 6, 0, tzinfo=UTC)
SINCE = datetime(2026, 9, 1, tzinfo=UTC)
AOS = AS_OF - timedelta(days=3)

RAW_TABLES = (
    "passes",
    "assignments",
    "observations",
    "heartbeats",
    "element_sets",
    "stations",
    "capabilities",
    "satellites",
    "transmitters",
    "archive_observations",
    "archive_stations",
    "ingest_records",
    "listening",
    "archive_passes",
    "pass_tracks",
    "environment_samples",
    "areas_of_interest",
    "pass_ground_tracks",
)
"""What an export writes: every snapshot table, the frozen listening answers,
the archive stations' computed passes, and our passes' sky and ground tracks."""

SOURCE = SourceEntry(
    source_id="reference_archive",
    licence="CC-BY-4.0",
    terms_url="https://example.invalid/terms",
    attribution_entry="The reference adapter's fixtures are ours",
    records=1,
)

RawSnapshot = Callable[[Mapping[str, Sequence[Mapping[str, object]]]], Path]


def _pass(pass_id: int, station: str, *, simulated: bool = False) -> dict[str, object]:
    return {
        "id": pass_id,
        "satellite_id": "norad:57166",
        "station_id": station,
        "aos": AOS,
        "los": AOS + timedelta(minutes=11),
        "max_elevation_deg": 40.0,
        "aos_azimuth_deg": 350.0,
        "los_azimuth_deg": 170.0,
        "element_set_id": 1,
        "computed_at": AOS - timedelta(hours=5),
        "simulated": simulated,
    }


def _assignment(
    pass_id: int, station: str, *, simulated: bool = False
) -> dict[str, object]:
    return {
        "assignment_id": f"as_{pass_id}",
        "pass_id": pass_id,
        "station_id": station,
        "start_at": AOS,
        "end_at": AOS + timedelta(minutes=11),
        "decision": "scheduled",
        "state": "reported",
        "model_config": "A",
        "simulated": simulated,
    }


def _observation(
    pass_id: int, outcome: str, *, simulated: bool = False
) -> dict[str, object]:
    return {
        "assignment_id": f"as_{pass_id}",
        "revision": 1,
        "outcome": outcome,
        "first_detection_at": None,
        "noise_floor_dbfs": None,
        "receiver_gain_db": None,
        "simulated": simulated,
    }


WORLD: Mapping[str, Sequence[Mapping[str, object]]] = {
    "passes": [
        _pass(1, "st_a"),
        _pass(2, "st_b"),
        _pass(3, "st_sim", simulated=True),
    ],
    "assignments": [
        _assignment(1, "st_a"),
        _assignment(2, "st_b"),
        _assignment(3, "st_sim", simulated=True),
    ],
    "observations": [
        _observation(1, "decoded"),
        _observation(2, "no_signal"),
        _observation(3, "decoded", simulated=True),
    ],
    "element_sets": [
        {"id": 1, "satellite_id": "norad:57166", "epoch": AOS - timedelta(hours=6)},
    ],
    "heartbeats": [
        {
            "station_id": "st_b",
            "received_at": AOS + timedelta(minutes=2),
            "clock_offset_s": None,
            "clock_uncertainty_s": None,
        },
    ],
    "transmitters": [
        {
            "id": 1,
            "satellite_id": "norad:57166",
            "centre_freq_hz": 137_900_000,
            "active": True,
            "deleted_at": None,
        },
    ],
    "listening": [
        {"assignment_id": "as_1", "listening_confirmed": True},
        {"assignment_id": "as_2", "listening_confirmed": True},
        {"assignment_id": "as_3", "listening_confirmed": True},
    ],
    "archive_observations": [
        {
            "archive_observation_id": 1,
            "archive_station_id": 1,
            "source_id": "reference_archive",
            "satellite_key": "norad:57166",
            "satellite_key_kind": "norad",
            "started_at": AOS + timedelta(hours=1),
            "archive_outcome": "no_data",
            "source_outcome": "nothing heard",
        },
    ],
}
"""Three passes of one satellite: one decoded, one confirmed silent (a miss,
because pass 1 heard the satellite), and one simulated — plus an archive row."""


ARCHIVE_DAY = datetime(2026, 9, 10, tzinfo=UTC)


def _archive_pass(day: int, hour: int, peak: float) -> dict[str, object]:
    aos = ARCHIVE_DAY + timedelta(days=day, hours=hour)
    return {
        "archive_station_id": 7,
        "satellite_id": "norad:25544",
        "aos": aos,
        "los": aos + timedelta(minutes=10),
        "max_elevation_deg": peak,
    }


def _heard(n: int, day: int, hour: int, outcome: str) -> dict[str, object]:
    return {
        "archive_observation_id": n,
        "archive_station_id": 7,
        "source_id": "reference_archive",
        "satellite_key": "norad:25544",
        "satellite_key_kind": "norad",
        "started_at": ARCHIVE_DAY + timedelta(days=day, hours=hour, seconds=30),
        "archive_outcome": outcome,
        "source_outcome": outcome,
    }


ARCHIVE_WORLD: Mapping[str, Sequence[Mapping[str, object]]] = {
    **WORLD,
    "stations": [
        {"station_id": "st_a", "lon_deg": 77.6},
        {"station_id": "st_b", "lon_deg": 77.6},
    ],
    "archive_stations": [{"archive_station_id": 7, "lon_deg": -1.5}],
    "archive_passes": [
        _archive_pass(0, 1, 40.0),
        _archive_pass(0, 5, 40.0),
        _archive_pass(0, 9, 10.0),
        _archive_pass(1, 3, 40.0),
        _archive_pass(2, 3, 40.0),
    ],
    "archive_observations": [
        _heard(11, 0, 1, "decoded"),
        _heard(12, 0, 5, "no_data"),
        _heard(13, 2, 3, "unknown"),
    ],
}
""":data:`WORLD`, and one archive station over three days. Day 0: three
passes, two heard — one decoded, one ``no_data`` — and the 10° one not, so it
is 2/3 complete and its low pass has no support. Day 1: nothing heard, so
``inactive``. Day 2: its one pass heard, outcome ``unknown`` — attempted but
not usable. Every 40° pass was attempted, so that cell is certain."""


@pytest.fixture
def archive_world() -> Mapping[str, Sequence[Mapping[str, object]]]:
    """:data:`ARCHIVE_WORLD`, for tests that cannot import from a conftest."""
    return ARCHIVE_WORLD


@pytest.fixture
def world() -> Mapping[str, Sequence[Mapping[str, object]]]:
    """:data:`WORLD`, for tests that cannot import from a conftest."""
    return WORLD


@pytest.fixture
def datasets_root(tmp_path: Path) -> Iterator[Path]:
    """A datasets root, made writable again afterwards so pytest can remove it."""
    root = tmp_path / "datasets"
    yield root
    for path in sorted(root.rglob("*"), reverse=True):
        path.chmod(0o700 if path.is_dir() else 0o600)


@pytest.fixture
def raw_snapshot(datasets_root: Path) -> RawSnapshot:
    """Publish a raw snapshot holding the given rows, every other table empty."""

    def publish(tables: Mapping[str, Sequence[Mapping[str, object]]]) -> Path:
        files = {
            f"{name}.jsonl": b"".join(
                canonical_line(row) for row in tables.get(name, ())
            )
            for name in RAW_TABLES
        }
        manifest = Manifest(
            kind="raw_snapshot",
            schema_revision="0016",
            since=SINCE,
            as_of=AS_OF,
            files=tuple(file_entry(name, data) for name, data in sorted(files.items())),
            created_at=AS_OF,
            sources=(SOURCE,) if tables.get("archive_observations") else (),
        )
        name = f"20260923T060000Z-{content_sha256(manifest).hex()[:12]}"
        return publish_directory(
            datasets_root / "snapshots", name, manifest, files
        ).path

    return publish


@dataclass
class NetworkGuard:
    """Refuses every database connection and socket, and remembers each attempt."""

    attempts: list[str] = field(default_factory=list)

    def refuse(self, what: str) -> OSError:
        self.attempts.append(what)
        return OSError(f"the gate test has no network; {what} was attempted")


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[NetworkGuard]:
    """Close every door a labelling run could use to reach a database."""
    guard = NetworkGuard()

    def refuse_psycopg(*_args: object, **_kwargs: object) -> None:
        raise guard.refuse("psycopg.connect")

    def refuse_socket(*_args: object, **_kwargs: object) -> None:
        raise guard.refuse("socket.connect")

    monkeypatch.setattr(psycopg, "connect", refuse_psycopg)
    monkeypatch.setattr(psycopg.Connection, "connect", refuse_psycopg)
    monkeypatch.setattr(socket.socket, "connect", refuse_socket)
    monkeypatch.setattr(socket, "create_connection", refuse_socket)
    yield guard


# --- a world a report can fit models on (Stage 22) --------------------------

EVALUATION_DAYS = 22
DAILY = "norad:57166"
RIVAL = "norad:59051"
CLASH_SLOT = 2

Tables = dict[str, list[Mapping[str, object]]]


def _decoded_world() -> Tables:
    """22 days over two stations, fittable, and a clash for the scheduler.

    Each station has eight passes a day of one satellite, decoding more often
    the higher it climbs, and once a day a second satellite rises six minutes
    after one of them; the historical policy took the higher of the two. The
    same shape as the scheduler gate's world (``test_scheduler_gate.py``), so a
    report's prediction and scheduling sections both have something to judge.
    Every row is measured: no model is ever fitted on a simulated one (D-078).
    """
    rng = random.Random(22)
    rows: Tables = {"passes": [], "assignments": [], "observations": []}
    for day in range(EVALUATION_DAYS):
        for offset, station in enumerate(("st_a", "st_b")):
            for slot in range(8):
                aos = SINCE + timedelta(days=day, hours=3 * slot + offset, minutes=10)
                taken = [_world_pass(rows, rng, (station, DAILY), aos)]
                if slot == CLASH_SLOT:
                    later = aos + timedelta(minutes=6)
                    rival = _world_pass(rows, rng, (station, RIVAL), later)
                    taken = [max((taken[0], rival), key=lambda one: one[1])]
                for number, _peak, decoded in taken:
                    _world_report(rows, number, decoded, rng)
    return rows | {
        "element_sets": [
            {
                "id": 2 * day + index,
                "satellite_id": satellite,
                "epoch": SINCE + timedelta(days=day),
            }
            for day in range(EVALUATION_DAYS)
            for index, satellite in enumerate((DAILY, RIVAL))
        ],
        "stations": [{"station_id": one, "lon_deg": 77.6} for one in ("st_a", "st_b")],
        "satellites": [
            {"satellite_id": DAILY, "priority": 1.0},
            {"satellite_id": RIVAL, "priority": 1.5},
        ],
        "transmitters": [
            {
                "id": index + 1,
                "satellite_id": one,
                "centre_freq_hz": 137_900_000,
                "active": True,
                "deleted_at": None,
            }
            for index, one in enumerate((DAILY, RIVAL))
        ],
    }


def _world_pass(
    rows: Tables, rng: random.Random, who: tuple[str, str], aos: datetime
) -> tuple[int, float, bool]:
    """Add a predicted pass; return its id, its peak and whether it decodes."""
    station, satellite = who
    number = len(rows["passes"]) + 1
    peak = rng.uniform(5.0, 85.0)
    odds = 0.9 if satellite == RIVAL else 1.0
    decoded = rng.random() < odds / (1 + math.exp(-(peak - 35.0) / 10.0))
    rows["passes"].append(
        {
            "id": number,
            "satellite_id": satellite,
            "station_id": station,
            "aos": aos,
            "los": aos + timedelta(minutes=12),
            "max_elevation_deg": peak,
            "aos_azimuth_deg": rng.uniform(0.0, 360.0),
            "los_azimuth_deg": rng.uniform(0.0, 360.0),
            "element_set_id": 2 * (aos - SINCE).days + (satellite == RIVAL),
            "computed_at": aos - timedelta(hours=5),
            "simulated": False,
        }
    )
    return number, peak, decoded


def _world_report(rows: Tables, number: int, decoded: bool, rng: random.Random) -> None:
    """The assignment the historical policy made, and the station's report."""
    predicted = rows["passes"][number - 1]
    frames = rng.randint(20, 400) if decoded else 0
    rows["assignments"].append(
        {
            "assignment_id": f"as_{number}",
            "pass_id": number,
            "station_id": predicted["station_id"],
            "start_at": predicted["aos"],
            "end_at": predicted["los"],
            "decision": "scheduled",
            "state": "reported",
            "model_config": "B",
            "simulated": False,
        }
    )
    rows["observations"].append(
        {
            "assignment_id": f"as_{number}",
            "revision": 1,
            "outcome": "decoded" if decoded else "signal_no_decode",
            "first_detection_at": None,
            "noise_floor_dbfs": None,
            "receiver_gain_db": None,
            "frames_decoded": frames,
            "simulated": False,
        }
    )


EVALUATION_WORLD = _decoded_world()


@pytest.fixture
def evaluation_world() -> Mapping[str, Sequence[Mapping[str, object]]]:
    """:func:`_decoded_world`, built once: a world every configuration fits on."""
    return EVALUATION_WORLD


# --- Stage 26: a world of rated receptions, for the reception verdict ---------

VERDICT_TABLES = (*RAW_TABLES, "products", "reception_ratings")
"""A raw snapshot from after migration 0027: products and ratings too."""


def _verdict_world() -> Tables:
    """Receptions every two hours from ``SINCE`` to ``AS_OF``, rated where decoded.

    The usable rate rises with SNR, so a verdict has something to learn. Every
    fifth reception reports no decoder statistics (MSP 0.2), a weak one hears
    nothing and reports no SNR, and every seventeenth decoded one is unrated.
    Five simulated receptions, rated by hand in the file, must never be read.
    """
    rng = random.Random(2026)
    rows: Tables = {name: [] for name in VERDICT_TABLES}
    rows["transmitters"].append(
        {
            "id": 1,
            "satellite_id": "norad:57166",
            "centre_freq_hz": 137100000,
            "mode": "lrpt",
            "active": True,
            "deleted_at": None,
            "frame_interval_s": 0.113778,
        }
    )
    start = SINCE + timedelta(hours=1)
    number = 0
    while start < AS_OF - timedelta(hours=1):
        number += 1
        _verdict_reception(rows, number, start, rng, simulated=number % 53 == 0)
        start += timedelta(hours=2)
    return rows


def _verdict_reception(
    rows: Tables, number: int, start: datetime, rng: random.Random, *, simulated: bool
) -> None:
    station = "st_sim" if simulated else "st_001"
    name = f"as_{number}"
    snr = rng.uniform(0.0, 20.0)
    outcome = "no_signal" if snr < 3 else "signal_no_decode" if snr < 6 else "decoded"
    statistics = number % 5 != 0
    ratio = min(1.0, max(0.0, (snr - 6.0) / 10.0 + rng.uniform(-0.1, 0.1)))
    rows["passes"].append(
        {
            "id": number,
            "satellite_id": "norad:57166",
            "station_id": station,
            "aos": start,
            "los": start + timedelta(minutes=10),
        }
    )
    rows["assignments"].append(
        {
            "assignment_id": name,
            "pass_id": number,
            "station_id": station,
            "centre_freq_hz": 137100000,
            "mode": "lrpt",
            "decision": "scheduled",
            "simulated": simulated,
        }
    )
    rows["listening"].append(
        {"assignment_id": name, "listening_confirmed": number % 11 != 0}
    )
    decoded = outcome == "decoded"
    rows["observations"].append(
        {
            "assignment_id": name,
            "revision": 1,
            "station_id": station,
            "satellite_id": "norad:57166",
            "started_at": start,
            "outcome": outcome,
            "signal_detected": outcome != "no_signal",
            "peak_snr_db": None if outcome == "no_signal" else round(snr, 2),
            "frames_decoded": round(5273 * ratio)
            if statistics and decoded
            else (0 if statistics else None),
            "decoder": "satdump" if statistics else None,
            "decoder_version": "1.2.2" if statistics else None,
            "simulated": simulated,
        }
    )
    if not decoded:
        return
    rows["products"].append(
        {"assignment_id": name, "revision": 1, "kind": "image", "simulated": simulated}
    )
    if number % 17 == 0:
        return
    usable = rng.random() < 1.0 / (1.0 + math.exp(-(snr - 11.0) / 1.5))
    rows["reception_ratings"].append(
        {
            "id": number,
            "assignment_id": name,
            "revision": 1,
            "usable": usable,
            "rubric": "usable-1",
            "rated_at": start + timedelta(hours=1),
            "simulated": simulated,
        }
    )


VERDICT_WORLD = _verdict_world()


@pytest.fixture
def verdict_world() -> Mapping[str, Sequence[Mapping[str, object]]]:
    """:func:`_verdict_world`, built once."""
    return VERDICT_WORLD


@pytest.fixture
def verdict_snapshot(datasets_root: Path) -> Callable[[Tables], Path]:
    """Publish a raw snapshot from after migration 0027 holding the given rows."""

    def publish(tables: Tables) -> Path:
        files = {
            f"{name}.jsonl": b"".join(
                canonical_line(row) for row in tables.get(name, ())
            )
            for name in VERDICT_TABLES
        }
        manifest = Manifest(
            kind="raw_snapshot",
            schema_revision="0027",
            since=SINCE,
            as_of=AS_OF,
            files=tuple(file_entry(name, data) for name, data in sorted(files.items())),
            created_at=AS_OF,
        )
        name = f"20260923T060000Z-{content_sha256(manifest).hex()[:12]}"
        return publish_directory(
            datasets_root / "snapshots", name, manifest, files
        ).path

    return publish


EVIDENCE_TABLES = (*VERDICT_TABLES, "reception_verdicts", "loss_diagnoses")
"""A raw snapshot from after migration 0031: verdicts and diagnoses as well."""

EVIDENCE_AOS = AS_OF - timedelta(days=2)


def _evidence_pass(number: int, station: str, aos: datetime) -> dict[str, object]:
    return {
        "id": number,
        "satellite_id": "norad:57166",
        "station_id": station,
        "aos": aos,
        "los": aos + timedelta(minutes=11),
        "max_elevation_deg": 40.0 + number,
        "max_elevation_at": aos + timedelta(minutes=5),
        "aos_azimuth_deg": 350.0,
        "los_azimuth_deg": 170.0,
        "element_set_id": 1,
        "min_elevation_deg": 10.0,
        "computed_at": aos - timedelta(hours=5),
        "simulated": station == "st_sim",
    }


def _evidence_assignment(
    number: int, station: str, aos: datetime, **columns: object
) -> dict[str, object]:
    return {
        "assignment_id": f"as_{number}",
        "pass_id": number,
        "station_id": station,
        "issued_at": aos - timedelta(hours=4),
        "start_at": aos,
        "end_at": aos + timedelta(minutes=11),
        "centre_freq_hz": 137_900_000,
        "mode": "lrpt",
        "timing_uncertainty_s": 4.2,
        "predicted_yield": 0.7,
        "priority": 1.0,
        "decision": "scheduled",
        "reason": "highest elevation in its window",
        "model_config": "A",
        "score": 0.6,
        "conflicts_with_assignment_id": None,
        "state": "reported",
        "simulated": station == "st_sim",
        "revision": 1,
        "revoked_reason": None,
    } | columns


def _evidence_observation(
    number: int, revision: int, outcome: str, aos: datetime, station: str = "st_a"
) -> dict[str, object]:
    return {
        "assignment_id": f"as_{number}",
        "revision": revision,
        "observation_id": f"ob_{number}_{revision}",
        "station_id": station,
        "satellite_id": "norad:57166",
        "started_at": aos,
        "ended_at": aos + timedelta(minutes=11),
        "outcome": outcome,
        "signal_detected": outcome != "no_signal",
        "first_detection_at": aos + timedelta(seconds=40),
        "peak_snr_db": 9.5 + revision,
        "doppler_samples": [{"t": aos + timedelta(minutes=5), "hz": 1210.5}],
        "products_json": [{"kind": "image", "uri": "file:///private/path.png"}],
        "client_notes": "private note from the station",
        "simulated": station == "st_sim",
        "provenance": "station",
        "submitted_at": aos + timedelta(minutes=20 + revision),
        "content_sha256": bytes([number * 16 + revision]) * 32,
        "noise_floor_dbfs": -52.3,
        "receiver_gain_db": 32.8,
        "snr_samples": [{"t": aos + timedelta(minutes=4), "snr_db": 3.1}],
        "decoder": "satdump",
        "decoder_version": "1.2.2",
        "frames_decoded": 400 if outcome == "decoded" else 0,
        "frames_failed": 12,
    }


def _evidence_world() -> Tables:
    late = AS_OF - timedelta(minutes=5)
    rows: Tables = {name: [] for name in EVIDENCE_TABLES}
    rows["stations"] = [
        {
            "station_id": "st_a",
            "name": "Roof of the north block",
            "lat_deg": 12.971599,
            "lon_deg": 77.594566,
            "alt_m": 920.0,
            "registered_at": SINCE,
            "location_precision_decimals": 2,
            "simulated": False,
            "simulator_run_id": None,
            "seed": None,
            "client_implementation": "meridian-client",
            "client_version": "0.3.0",
            "deleted_at": None,
        },
        {
            "station_id": "st_sim",
            "name": "virtual 1",
            "lat_deg": 13.0,
            "lon_deg": 77.6,
            "alt_m": 900.0,
            "registered_at": SINCE,
            "location_precision_decimals": 2,
            "simulated": True,
            "simulator_run_id": "run-a",
            "seed": 4471,
            "client_implementation": "meridian-sim",
            "client_version": "0.3.0",
            "deleted_at": None,
        },
    ]
    rows["element_sets"] = [
        {
            "id": 1,
            "satellite_id": "norad:57166",
            "epoch": EVIDENCE_AOS - timedelta(hours=6),
            "retrieved_at": EVIDENCE_AOS - timedelta(hours=5),
            "line1": (
                "1 57166U 23091A   26264.00000000  .00000000  00000-0  00000-0 0  9990"
            ),
            "line2": (
                "2 57166  98.7000 120.0000 0001000  90.0000 270.0000 14.20000000 10000"
            ),
            "source": "celestrak",
            "content_sha256": bytes([9]) * 32,
        }
    ]
    plan = [
        # Decoded on its second revision, after a first that only heard signal.
        (1, "st_a", EVIDENCE_AOS, {}),
        # Held to the end of its window with no report: a miss.
        (2, "st_a", EVIDENCE_AOS + timedelta(hours=2), {"state": "held"}),
        # A decline, revoked work, a skipped pass, an open window, unacknowledged.
        (3, "st_a", EVIDENCE_AOS + timedelta(hours=4), {"state": "expired"}),
        (
            4,
            "st_a",
            EVIDENCE_AOS + timedelta(hours=6),
            {"state": "revoked", "revoked_reason": "station_offline"},
        ),
        (5, "st_a", EVIDENCE_AOS + timedelta(hours=8), {"decision": "skipped"}),
        (7, "st_a", late, {"state": "held"}),
        (8, "st_a", EVIDENCE_AOS + timedelta(hours=10), {"state": "issued"}),
        # A simulated station's decode.
        (6, "st_sim", EVIDENCE_AOS + timedelta(hours=3), {}),
    ]
    for number, station, aos, columns in plan:
        rows["passes"].append(_evidence_pass(number, station, aos))
        rows["assignments"].append(
            _evidence_assignment(number, station, aos, **columns)
        )
    rows["observations"] = [
        _evidence_observation(1, 1, "signal_no_decode", EVIDENCE_AOS),
        _evidence_observation(1, 2, "decoded", EVIDENCE_AOS),
        _evidence_observation(
            6, 1, "decoded", EVIDENCE_AOS + timedelta(hours=3), station="st_sim"
        ),
    ]
    rows["listening"] = [
        {"assignment_id": "as_1", "station_id": "st_a", "listening_confirmed": True},
        {"assignment_id": "as_2", "station_id": "st_a", "listening_confirmed": False},
    ]
    rows["reception_verdicts"] = [
        {
            "assignment_id": "as_1",
            "revision": revision,
            "observation_started_at": EVIDENCE_AOS,
            "station_id": "st_a",
            "probability_usable": probability,
            "method": method,
            "route": "full",
            "inputs_sha256": bytes([revision]) * 32,
            "partial_below": 0.4,
            "computed_at": EVIDENCE_AOS + timedelta(hours=1),
            "simulated": False,
        }
        for revision, method, probability in (
            (1, "verdict-1:aaaaaaaaaaaa", 0.2),
            (2, "verdict-1:aaaaaaaaaaaa", 0.81),
            (2, "verdict-2:bbbbbbbbbbbb", 0.77),
        )
    ]
    rows["loss_diagnoses"] = [
        {
            "diagnosis_id": 11,
            "assignment_id": "as_2",
            "revision": None,
            "observation_started_at": None,
            "station_id": "st_a",
            "classification_id": 5,
            "cause": "station_not_listening",
            "candidates_json": [{"cause": "station_not_listening", "fired": True}],
            "evidence_json": {"listening": False},
            "method": "diagnosis-1",
            "config_sha256": bytes([6]) * 32,
            "verdict_method": None,
            "computed_at": AS_OF - timedelta(hours=1),
            "simulated": False,
        }
    ]
    rows["products"] = [
        {
            "id": index,
            "assignment_id": "as_1",
            "revision": revision,
            "observation_started_at": EVIDENCE_AOS,
            "station_id": "st_a",
            "element_index": element,
            "kind": kind,
            "sha256": bytes([index]) * 32,
            "size_bytes": 1000 * index,
            "created_at": EVIDENCE_AOS + timedelta(minutes=30),
            "simulated": False,
        }
        for index, revision, element, kind in (
            (1, 1, 0, "frames"),
            (2, 2, 0, "image"),
            (3, 2, 1, "frames"),
        )
    ]
    return rows


EVIDENCE_WORLD = _evidence_world()


@pytest.fixture
def evidence_world() -> Tables:
    """Receptions of every kind a package must keep or leave out, every column."""
    return {name: [dict(row) for row in rows] for name, rows in EVIDENCE_WORLD.items()}


@pytest.fixture
def evidence_snapshot(datasets_root: Path) -> Callable[[Tables], Path]:
    """Publish a raw snapshot from after migration 0031 holding the given rows."""

    def publish(tables: Tables) -> Path:
        files = {
            f"{name}.jsonl": b"".join(
                canonical_line(row) for row in tables.get(name, ())
            )
            for name in EVIDENCE_TABLES
        }
        manifest = Manifest(
            kind="raw_snapshot",
            schema_revision="0031",
            since=SINCE,
            as_of=AS_OF,
            files=tuple(file_entry(name, data) for name, data in sorted(files.items())),
            created_at=AS_OF,
        )
        name = f"20260923T060000Z-{content_sha256(manifest).hex()[:12]}"
        return publish_directory(
            datasets_root / "snapshots", name, manifest, files
        ).path

    return publish
