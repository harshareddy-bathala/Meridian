"""``meridian.cli_catalogue.load_document`` against real TimescaleDB.

What makes this worth an integration test rather than a unit one is idempotency:
the loader writes nothing on a second run, and it gets that from a primary key,
a partial unique index and a content hash — three database facts, none of which
can be checked without a database.

Marked ``integration`` by the directory hook in ``tests/conftest.py``. Same
``rollback`` fixture pattern as ``test_store_element_sets.py``.

Reference: docs/DECISIONS.md D-079; migration 0013.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from meridian.catalogue_file import read_catalogue  # noqa: E402 — after importorskip
from meridian.cli_catalogue import load_document  # noqa: E402
from meridian.store.satellites import find_active_transmitters  # noqa: E402

pytestmark = pytest.mark.integration

SATELLITE_ID = "norad:57166"

LINE1 = "1 57166U 23091A   26223.50000000  .00000100  00000-0  50000-4 0  9990"
LINE2 = "2 57166  98.7041 210.4322 0002726  80.4113 279.7297 14.22000000160126"


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    """Undo everything this test writes — see test_store_invites.py's twin."""
    with conn.transaction(force_rollback=True):
        yield conn


def document(tmp_path: Path, **overrides: Any) -> Any:
    """One catalogue file on disk, parsed, with the fields a test varies exposed."""
    entry: dict[str, Any] = {
        "satellite_id": SATELLITE_ID,
        "name": "Meteor-M2-3",
        "transmitters": [
            {"centre_freq_hz": 137100000, "mode": "lrpt", "polarisation": "rhcp"}
        ],
        "element_sets": [{"line1": LINE1, "line2": LINE2}],
    }
    entry.update(overrides)
    path = tmp_path / "catalogue.json"
    path.write_text(json.dumps({"satellites": [entry]}), encoding="utf-8")
    return read_catalogue(path)


def counts(conn: Any) -> tuple[int, int, int]:
    """Rows this satellite has in each of the three tables."""
    with conn.cursor() as cur:
        cur.execute(
            """
            select
              (select count(*) from satellites where satellite_id = %s),
              (select count(*) from satellite_transmitters where satellite_id = %s),
              (select count(*) from element_sets where satellite_id = %s)
            """,
            (SATELLITE_ID, SATELLITE_ID, SATELLITE_ID),
        )
        row = cur.fetchone()
    return (int(row[0]), int(row[1]), int(row[2]))


def test_a_first_load_writes_every_row(rollback: Any, tmp_path: Path) -> None:
    """The three tables a catalogue describes, filled from one file."""
    tally = load_document(rollback, document(tmp_path))

    assert (
        tally.satellites_written,
        tally.transmitters_written,
        tally.element_sets_written,
    ) == (1, 1, 1)
    assert counts(rollback) == (1, 1, 1)


def test_loading_the_same_document_twice_writes_nothing(
    rollback: Any, tmp_path: Path
) -> None:
    """The property that makes this safe in a startup script (D-079).

    A duplicated transmitter is the damaging one: `find_active_transmitters`
    feeds pass generation, so it would compute every pass of the satellite
    twice and hand the scheduler two candidates competing for one station at one
    moment — a fabricated conflict written into the record.
    """
    parsed = document(tmp_path)
    load_document(rollback, parsed)

    again = load_document(rollback, parsed)

    assert again.wrote_nothing
    assert counts(rollback) == (1, 1, 1)


def test_a_new_element_set_loads_beside_the_old_one(
    rollback: Any, tmp_path: Path
) -> None:
    """The routine refresh: same satellite, same downlink, newer elements."""
    load_document(rollback, document(tmp_path))
    refit = LINE1[:33] + "9" + LINE1[34:-1]
    later = document(
        tmp_path,
        element_sets=[{"line1": refit + _checksum_digit(refit), "line2": LINE2}],
    )

    tally = load_document(rollback, later)

    assert (tally.satellites_written, tally.transmitters_written) == (0, 0)
    assert tally.element_sets_written == 1
    assert counts(rollback) == (1, 1, 2)


def test_a_second_downlink_is_a_second_row(rollback: Any, tmp_path: Path) -> None:
    """Identity is satellite, frequency and mode — a new frequency is new."""
    load_document(rollback, document(tmp_path))
    two = document(
        tmp_path,
        transmitters=[
            {"centre_freq_hz": 137100000, "mode": "lrpt"},
            {"centre_freq_hz": 137900000, "mode": "lrpt"},
        ],
    )

    tally = load_document(rollback, two)

    assert tally.transmitters_written == 1
    assert {one.centre_freq_hz for one in find_active_transmitters(rollback)} >= {
        137100000,
        137900000,
    }


def test_a_repeat_load_does_not_rewrite_a_renamed_satellite(
    rollback: Any, tmp_path: Path
) -> None:
    """Re-running an import must not quietly rewrite what passes already reference.

    Changing a tracked object is an operator action with consequences of its
    own, not a side effect of loading a file somebody edited.
    """
    load_document(rollback, document(tmp_path))

    load_document(rollback, document(tmp_path, name="Something Else"))

    with rollback.cursor() as cur:
        cur.execute(
            "select name from satellites where satellite_id = %s", (SATELLITE_ID,)
        )
        assert cur.fetchone()[0] == "Meteor-M2-3"


def test_the_shipped_development_catalogue_loads(rollback: Any) -> None:
    """`docker compose --profile sim up` depends on this file reaching the archive.

    A failure here is the one that would otherwise appear as a simulator which
    registers happily and is never given anything to do.
    """
    tally = load_document(
        rollback, read_catalogue(Path("deploy/catalogue/development.json"))
    )

    assert tally.satellites_written == 2
    assert tally.transmitters_written == 2
    assert tally.element_sets_written == 2
    assert len(find_active_transmitters(rollback)) >= 2


def _checksum_digit(body: str) -> str:
    """The modulo-10 digit closing an element-set line built by a test."""
    total = sum(
        int(column) if column.isdigit() else (1 if column == "-" else 0)
        for column in body
    )
    return str(total % 10)
