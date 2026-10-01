"""Stage 26's gate, the database's half: every measured reception has a verdict.

The roadmap's gate reads: *every measured reception carries a versioned
verdict, and SC-7's report regenerates from a snapshot, a configuration and a
seed.* The report half is ``tests/unit/test_report_verdict.py``. This half
runs the whole loop on a real database:
1. receptions are stored;
2. the decoded ones are rated blind;
3. a raw snapshot is exported;
4. the verdict is fitted from it and published;
5. the model is applied.

Then every closed, scheduled measured reception has a verdict, with the
model's method. So does a simulated one, labelled simulated, while it never
reached the fit. A positive control removes one verdict and finds the check
fails.

Reference: docs/DECISIONS.md D-260 to D-263.
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("sklearn")

from meridian.datasets.export import export_snapshot, read_snapshot  # noqa: E402
from meridian.datasets.publish import read_directory  # noqa: E402
from meridian.datasets.usable_labels import read_usable_labels  # noqa: E402
from meridian.prediction.fit import fit_verdict  # noqa: E402
from meridian.prediction.verdict_config import VerdictConfig  # noqa: E402
from meridian.prediction.verdict_examples import build_verdict_examples  # noqa: E402
from meridian.prediction.verdict_files import (  # noqa: E402
    load_verdict_model,
    publish_verdict,
)
from meridian.prediction.verdict_rows import read_receptions  # noqa: E402
from meridian.registry import ListeningQuery  # noqa: E402
from meridian.store.ratings import NewRating, insert_rating  # noqa: E402
from meridian.verdict_build import apply_verdicts  # noqa: E402

pytestmark = pytest.mark.integration

RECEPTIONS = 150
DAYS = 15


class Listening:
    def was_listening(self, query: ListeningQuery) -> bool:
        return query.station_id == "st_gate"


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def root(tmp_path: Path) -> Iterator[Path]:
    yield tmp_path
    for path in sorted(tmp_path.rglob("*"), reverse=True):
        path.chmod(0o700 if path.is_dir() else 0o600)


@pytest.fixture
def world(rollback: Any, schedule_rows: Any) -> tuple[Any, datetime]:
    """150 measured receptions over fifteen days, rated where decoded, and one
    simulated reception, rated in the database by hand, that must never count.
    """
    rng = random.Random(260)
    start = rollback.execute("select now()").fetchone()[0] - timedelta(days=DAYS + 1)
    measured = schedule_rows.station("st_gate", simulated=False)
    simulated = schedule_rows.station("st_gate_sim", simulated=True)
    element_set = schedule_rows.satellite()
    rollback.execute(
        "insert into satellite_transmitters (satellite_id, centre_freq_hz, mode,"
        " frame_interval_s) values ('norad:99970', 137900000, 'lrpt', 0.113778)"
    )
    step = timedelta(days=DAYS) / RECEPTIONS
    for number in range(RECEPTIONS):
        name = f"as_gate_{number}"
        pass_id = schedule_rows.pass_(
            measured, start + number * step, element_set_id=element_set
        )
        schedule_rows.assignment(name, pass_id)
        snr = rng.uniform(0.0, 20.0)
        outcome = "decoded" if snr >= 6 else "signal_no_decode"
        schedule_rows.observation(name, outcome=outcome)
        statistics = number % 3 != 0
        rollback.execute(
            "update observations set peak_snr_db = %s, decoder = %s,"
            " decoder_version = %s, frames_decoded = %s, frames_failed = %s"
            " where assignment_id = %s",
            (
                round(snr, 2),
                "satdump" if statistics else None,
                "1.2.2" if statistics else None,
                round(5800 * min(1.0, snr / 20.0))
                if statistics and snr >= 6
                else (0 if statistics else None),
                10 if statistics else None,
                name,
            ),
        )
        if outcome != "decoded":
            continue
        _product(rollback, name)
        usable = rng.random() < 1.0 / (1.0 + 2.718281828 ** (-(snr - 12.0) / 1.5))
        insert_rating(rollback, NewRating(name, 1, usable, "usable-1", "gate"))
    sim_pass = schedule_rows.pass_(
        simulated, start + timedelta(days=3), element_set_id=element_set
    )
    schedule_rows.assignment("as_gate_sim", sim_pass)
    schedule_rows.observation("as_gate_sim")
    _product(rollback, "as_gate_sim")
    rollback.execute(
        "insert into reception_ratings (assignment_id, revision,"
        " observation_started_at, station_id, usable, rubric, rater, simulated)"
        " select assignment_id, revision, started_at, station_id, true,"
        " 'usable-1', 'gate', true from observations"
        " where assignment_id = 'as_gate_sim'"
    )
    return rollback, start


def _product(conn: Any, name: str) -> None:
    conn.execute(
        "insert into products (assignment_id, revision, observation_started_at,"
        " station_id, element_index, kind, sha256, simulated)"
        " select assignment_id, revision, started_at, station_id, 0, 'image',"
        " sha256(convert_to(assignment_id, 'UTF8')), simulated"
        " from observations where assignment_id = %s",
        (name,),
    )


def missing(conn: Any, method: str) -> list[str]:
    """Closed, scheduled, measured receptions without a verdict by ``method``."""
    return [
        row[0]
        for row in conn.execute(
            "select o.assignment_id from observations o"
            " join assignments a on a.assignment_id = o.assignment_id"
            " where not o.simulated and a.decision = 'scheduled'"
            " and a.end_at <= now()"
            " and not exists (select 1 from reception_verdicts v"
            "  where v.assignment_id = o.assignment_id"
            "  and v.revision = o.revision and v.method = %s)",
            (method,),
        ).fetchall()
    ]


def test_every_measured_reception_carries_a_versioned_verdict(
    world: tuple[Any, datetime], root: Path
) -> None:
    conn, start = world
    listening = Listening()
    read = read_snapshot(conn, listening, since=start - timedelta(days=1))
    snapshot = read_directory(
        export_snapshot(read, root=root, created_at=datetime.now(UTC)).path
    )
    found = build_verdict_examples(
        read_receptions(snapshot.files),
        read_usable_labels(snapshot.files),
        rubric="usable-1",
    )
    config = VerdictConfig(
        train_until=start + timedelta(days=8),
        validate_until=start + timedelta(days=12),
    )
    fitted = fit_verdict(found, config, as_of=snapshot.manifest.as_of)
    published = publish_verdict(
        fitted,
        snapshot=snapshot,
        config=config,
        root=root,
        created_at=datetime.now(UTC),
    )
    model = load_verdict_model(published.path)

    report = apply_verdicts(conn, listening, model, now=snapshot.manifest.as_of)

    assert found.simulated == 1
    assert report.written == RECEPTIONS + 1
    assert missing(conn, model.method) == []
    (simulated,) = conn.execute(
        "select simulated from reception_verdicts where assignment_id = 'as_gate_sim'"
    ).fetchone()
    assert simulated is True
    methods = conn.execute(
        "select distinct method from reception_verdicts"
        " where assignment_id like 'as_gate_%%'"
    ).fetchall()
    assert methods == [(model.method,)]

    conn.execute("delete from reception_verdicts where assignment_id = 'as_gate_0'")
    assert missing(conn, model.method) == ["as_gate_0"]
