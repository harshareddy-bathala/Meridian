"""``loss_diagnoses`` — which losses are found, and each written once.

Marked ``integration`` by the directory hook. What only a database shows: the
scope query finds a failed reception, a partial one and an empty window, and
never an expired, revoked, decoded or unclassified one (D-272); a row is
written once per method and configuration, an empty window included, since the
key holds nulls as equal; the table's checks refuse what no diagnosis writes;
and a raw snapshot carries the rows, measured and simulated apart.

Reference: docs/DECISIONS.md D-008, D-104, D-171, D-272.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from meridian.datasets.export import export_snapshot, read_snapshot  # noqa: E402
from meridian.datasets.publish import read_directory  # noqa: E402
from meridian.registry import ListeningQuery  # noqa: E402
from meridian.store.loss_diagnoses import (  # noqa: E402
    NewDiagnosis,
    find_diagnoses,
    find_undiagnosed,
    insert_diagnosis,
)

pytestmark = pytest.mark.integration

CLOSED = datetime(2026, 8, 14, 9, 0, tzinfo=UTC)
SINCE = datetime(2026, 8, 1, tzinfo=UTC)
CREATED = datetime(2026, 9, 23, 7, 0, tzinfo=UTC)
CLASSIFICATION = "classification-1"
CLASSIFIED_UNDER = bytes([7]) * 32
METHOD = "diagnosis-1"
CONFIG = bytes([9]) * 32
VERDICT = "verdict-1:0123456789ab"


class Listening:
    def was_listening(self, _query: ListeningQuery) -> bool:
        return True


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def root(tmp_path: Path) -> Iterator[Path]:
    yield tmp_path
    for path in sorted(tmp_path.rglob("*"), reverse=True):
        path.chmod(0o700 if path.is_dir() else 0o600)


def classify(conn: Any, assignment_id: str, classification: str, **kw: Any) -> None:
    """One Stage 20 classification of the pass ``assignment_id`` is on."""
    conn.execute(
        "insert into pass_classifications (assignment_id, assignment_ids, pass_id,"
        " station_id, satellite_id, window_start, window_end, classification,"
        " evidence, method, config_sha256, simulated)"
        " select a.assignment_id, array[a.assignment_id], a.pass_id, a.station_id,"
        " p.satellite_id, a.start_at, a.end_at, %s, %s, %s, %s, a.simulated"
        " from assignments a join passes p on p.id = a.pass_id"
        " where a.assignment_id = %s",
        (
            classification,
            json.dumps({"heard_during_window": True, "listening_confirmed": True}),
            kw.get("method", CLASSIFICATION),
            kw.get("config_sha256", CLASSIFIED_UNDER),
            assignment_id,
        ),
    )


def verdict(conn: Any, assignment_id: str, probability: float) -> None:
    conn.execute(
        "insert into reception_verdicts (assignment_id, revision,"
        " observation_started_at, station_id, probability_usable, method, route,"
        " inputs_sha256, partial_below, simulated)"
        " select assignment_id, revision, started_at, station_id, %s, %s, 'full',"
        " %s, 0.4, simulated from observations_current where assignment_id = %s",
        (probability, VERDICT, bytes(32), assignment_id),
    )


@pytest.fixture
def world(rollback: Any, schedule_rows: Any) -> Any:
    """One reception of each kind the scope query must tell apart."""
    measured = schedule_rows.station("st_loss", simulated=False)
    simulated = schedule_rows.station("st_loss_sim", simulated=True)
    element_set = schedule_rows.satellite()
    kinds = (
        ("as_failed", measured, "reported", "no_signal", "confirmed_miss"),
        ("as_heard", measured, "reported", "signal_no_decode", "signal_no_decode"),
        ("as_partial", measured, "reported", "decoded", "successful_reception"),
        ("as_good", measured, "reported", "decoded", "successful_reception"),
        ("as_plain", measured, "reported", "decoded", "successful_reception"),
        ("as_empty", measured, "held", None, "station_not_confirmed_listening"),
        ("as_expired", measured, "expired", None, "assignment_declined"),
        ("as_revoked", measured, "issued", None, "assignment_declined"),
        ("as_unclassified", measured, "reported", "no_signal", None),
        ("as_sim", simulated, "reported", "no_signal", "confirmed_miss"),
    )
    for hour, (name, station, state, outcome, classification) in enumerate(kinds):
        pass_id = schedule_rows.pass_(
            station, CLOSED + timedelta(hours=hour), element_set_id=element_set
        )
        schedule_rows.assignment(name, pass_id, state=state)
        if outcome is not None:
            schedule_rows.observation(name, outcome=outcome)
        if classification is not None:
            classify(rollback, name, classification)
    rollback.execute(
        "update assignments set state = 'revoked', revoked_reason = 'declined',"
        " revoked_at = start_at - interval '1 hour'"
        " where assignment_id = 'as_revoked'"
    )
    verdict(rollback, "as_partial", 0.2)
    verdict(rollback, "as_good", 0.9)
    return rollback


def subjects(conn: Any, **changes: Any) -> list[str]:
    arguments = {
        "method": METHOD,
        "config_sha256": CONFIG,
        "classification_method": CLASSIFICATION,
        "classification_sha256": CLASSIFIED_UNDER,
        "verdict_method": VERDICT,
    } | changes
    return [one.assignment_id for one in find_undiagnosed(conn, **arguments)]


def diagnosis(conn: Any, assignment_id: str, **changes: Any) -> NewDiagnosis:
    (subject,) = (
        one
        for one in find_undiagnosed(
            conn,
            method=METHOD,
            config_sha256=changes.get("config_sha256", CONFIG),
            classification_method=CLASSIFICATION,
            classification_sha256=CLASSIFIED_UNDER,
            verdict_method=VERDICT,
        )
        if one.assignment_id == assignment_id
    )
    row = NewDiagnosis(
        assignment_id=subject.assignment_id,
        revision=subject.revision,
        observation_started_at=subject.observation_started_at,
        station_id=subject.station_id,
        classification_id=subject.classification_id,
        cause="undetermined",
        candidates=[{"cause": "obstruction", "fired": False}],
        evidence={"parameters": {}},
        method=METHOD,
        config_sha256=CONFIG,
        verdict_method=None,
        simulated=subject.simulated,
    )
    return replace(row, **changes)


def test_every_kind_of_loss_is_found_and_nothing_else(world: Any) -> None:
    assert subjects(world) == [
        "as_failed",
        "as_heard",
        "as_partial",
        "as_empty",
        "as_sim",
    ]


def test_without_a_verdict_model_no_decode_is_partial(world: Any) -> None:
    assert "as_partial" not in subjects(world, verdict_method=None)
    assert "as_partial" not in subjects(world, verdict_method="verdict-2:aaaaaaaaaaaa")


def test_a_loss_waits_for_its_classification_under_the_deployed_one(
    world: Any,
) -> None:
    assert subjects(world, classification_sha256=bytes(32)) == []
    assert subjects(world, classification_method="classification-9") == []


def test_what_the_subject_carries(world: Any) -> None:
    by_id = {
        one.assignment_id: one
        for one in find_undiagnosed(
            world,
            method=METHOD,
            config_sha256=CONFIG,
            classification_method=CLASSIFICATION,
            classification_sha256=CLASSIFIED_UNDER,
            verdict_method=VERDICT,
        )
    }

    assert by_id["as_empty"].revision is None
    assert by_id["as_empty"].outcome is None
    assert by_id["as_failed"].revision == 1
    assert by_id["as_failed"].classification == "confirmed_miss"
    assert by_id["as_failed"].classification_evidence["listening_confirmed"] is True
    assert by_id["as_partial"].probability_usable == 0.2
    assert by_id["as_partial"].partial_below == 0.4
    assert by_id["as_sim"].simulated is True
    assert by_id["as_failed"].simulated is False


def test_a_diagnosis_is_written_once_an_empty_window_included(world: Any) -> None:
    for name in ("as_failed", "as_empty"):
        row = diagnosis(world, name)
        assert insert_diagnosis(world, row)
        assert not insert_diagnosis(world, row)

    assert "as_failed" not in subjects(world)
    assert "as_empty" not in subjects(world)
    assert len(find_diagnoses(world, "as_empty")) == 1


def test_a_changed_configuration_diagnoses_again_beside_the_old(world: Any) -> None:
    insert_diagnosis(world, diagnosis(world, "as_failed"))
    other = bytes([8]) * 32

    assert "as_failed" in subjects(world, config_sha256=other)
    insert_diagnosis(world, diagnosis(world, "as_failed", config_sha256=other))
    assert len(find_diagnoses(world, "as_failed")) == 2


def test_a_new_revision_is_a_new_loss(world: Any, schedule_rows: Any) -> None:
    insert_diagnosis(world, diagnosis(world, "as_failed"))
    schedule_rows.observation("as_failed", revision=2, outcome="no_signal")

    assert "as_failed" in subjects(world)


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"cause": "gremlins"}, "CheckViolation"),
        ({"method": "diagnosis"}, "CheckViolation"),
        ({"verdict_method": "verdict-1"}, "CheckViolation"),
        ({"config_sha256": b"short"}, "CheckViolation"),
        ({"observation_started_at": None}, "CheckViolation"),
        ({"revision": 7}, "ForeignKeyViolation"),
    ],
)
def test_the_table_refuses_what_no_diagnosis_writes(
    world: Any, changes: dict[str, Any], error: str
) -> None:
    row = diagnosis(world, "as_failed", **changes)

    with pytest.raises(getattr(psycopg.errors, error)), world.transaction():
        insert_diagnosis(world, row)


def test_a_snapshot_carries_them_measured_and_simulated_apart(
    world: Any, root: Path
) -> None:
    for name in ("as_failed", "as_empty", "as_sim"):
        insert_diagnosis(world, diagnosis(world, name))

    published = export_snapshot(
        read_snapshot(world, Listening(), since=SINCE), root=root, created_at=CREATED
    )
    files = read_directory(published.path).files
    exported = [
        json.loads(one) for one in files["loss_diagnoses.jsonl"].split(b"\n") if one
    ]

    assert sorted(one["assignment_id"] for one in exported) == [
        "as_empty",
        "as_failed",
        "as_sim",
    ]
    assert published.manifest.counts["loss_diagnoses.measured"] == 2
    assert published.manifest.counts["loss_diagnoses.simulated"] == 1
