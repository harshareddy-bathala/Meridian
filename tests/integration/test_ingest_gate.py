"""The completion gate's other half: the snapshot loads, with nothing fetched.

``tests/unit/test_ingest_gate.py`` proves a downloaded snapshot normalises
identically with no network. This proves the rest of the sentence — that it
reaches the archive tables the same way — against a real database, with the
fixtures deleted and ``network_guard`` installed.

**The guard exempts psycopg, and that exemption buys less than it looks like.**
``psycopg[binary]`` talks to Postgres through libpq, which opens its own
sockets in C; the socket patches never see them either way, as
``test_the_guard_cannot_see_inside_libpq`` asserts in the unit half. So the
exemption keeps ``psycopg.connect`` callable, and the claim this file supports
is the honest one: **nothing in Python reached a network**, and the only thing
that reached anything at all was libpq talking to the database it was given.

Loading the same tree twice is ``test_ingest_load.py``'s, and stays there. What
is here is what only holds once the archive is gone.

Reference: docs/SOFTWARE-IMPLEMENTATION-ROADMAP.md Stage 14; docs/DECISIONS.md
D-141, D-142.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

psycopg = pytest.importorskip("psycopg")

from meridian_ingest.adapters.protocol import (  # noqa: E402 — after importorskip
    FetchRequest,
)
from meridian_ingest.adapters.reference import (  # noqa: E402 — after importorskip
    FIXTURE_ROOT,
    ReferenceAdapter,
    ReferenceNormaliser,
)
from meridian_ingest.load import (  # noqa: E402 — after importorskip
    load_artefact,
    load_source,
)
from meridian_ingest.provenance import Provenance  # noqa: E402 — after importorskip
from meridian_ingest.raw_layout import MANIFEST_NAME  # noqa: E402 — after importorskip
from meridian_ingest.raw_manifest import (  # noqa: E402 — after importorskip
    MalformedManifestError,
)
from meridian_ingest.raw_store import RawStore  # noqa: E402 — after importorskip
from meridian_ingest.retrieval import (  # noqa: E402 — after importorskip
    FixtureRetriever,
)

pytestmark = pytest.mark.integration

SOURCE = "reference_archive"
RETRIEVED_AT = datetime(2026, 9, 20, 10, 11, 43, tzinfo=UTC)


@pytest.fixture
def rollback(conn: Any) -> Iterator[Any]:
    """Undo everything this test writes — see test_ingest_load.py's twin."""
    with conn.transaction(force_rollback=True):
        yield conn


@pytest.fixture
def snapshot(tmp_path: Path) -> Iterator[RawStore]:
    """One download of the reference archive, with the fixtures then deleted.

    The same fixture as the unit half, for the same reason: with the source
    gone, anything that reads past the raw store fails with a missing file
    instead of quietly succeeding.
    """
    fixtures = tmp_path / "fixtures"
    shutil.copytree(FIXTURE_ROOT, fixtures)
    store = RawStore(tmp_path / "raw")
    adapter = ReferenceAdapter()
    retriever = FixtureRetriever(fixtures)
    for remote in adapter.plan(FetchRequest()):
        retrieved = retriever.retrieve(remote)
        store.publish(
            Provenance(
                source_id=SOURCE,
                original_identifier=remote.original_identifier,
                source_version=adapter.source_version(retrieved),
                payload_kind=remote.payload_kind,
                retrieved_at=RETRIEVED_AT,
                media_type=retrieved.media_type,
                valid_from=remote.valid_from,
                valid_to=remote.valid_to,
            ),
            retrieved.chunks,
        )
    shutil.rmtree(fixtures)

    yield store

    for path in sorted(store.root.rglob("*"), reverse=True):
        path.chmod(0o700 if path.is_dir() else 0o600)


def counts(conn: Any) -> tuple[int, int, int]:
    """Records, stations and receptions, as three numbers to compare."""
    return tuple(  # type: ignore[return-value]
        conn.execute(f"select count(*) from {table}").fetchone()[0]
        for table in ("ingest_records", "archive_stations", "archive_observations")
    )


# --- the gate --------------------------------------------------------------


def test_a_deleted_archive_still_loads_from_the_raw_store(
    rollback: Any, snapshot: RawStore, network_guard
) -> None:
    """The stage in one test: fetched once, and everything after it is local."""
    network_guard(allow=("psycopg",))

    report = load_source(rollback, snapshot, SOURCE)

    assert report.records_written == 3
    assert report.stations_written == 4
    assert report.receptions_written == 7
    assert counts(rollback) == (3, 4, 7)


def test_loading_it_a_second_time_still_needs_nothing(
    rollback: Any, snapshot: RawStore, network_guard
) -> None:
    """The gate says "repeatedly", and that covers the load as well as normalising."""
    network_guard(allow=("psycopg",))
    load_source(rollback, snapshot, SOURCE)
    before = counts(rollback)

    again = load_source(rollback, snapshot, SOURCE)

    assert again.wrote_nothing() is True
    assert counts(rollback) == before


def test_every_loaded_record_can_say_where_it_came_from(
    rollback: Any, snapshot: RawStore, network_guard
) -> None:
    """The provenance view answering in one query, which is why it exists.

    A record whose licence or terms nobody wrote down is a record no evaluation
    may cite, so "is the provenance complete" has to be cheaper to ask than to
    skip.
    """
    network_guard(allow=("psycopg",))
    load_source(rollback, snapshot, SOURCE)

    incomplete = rollback.execute(
        "select count(*) from ingest_provenance "
        "where licence is null or terms_url is null or attribution_entry is null "
        "or source_version is null or retrieved_at is null"
    ).fetchone()[0]

    assert incomplete == 0
    assert (
        rollback.execute(
            "select count(*) from ingest_provenance where source_id = %s", (SOURCE,)
        ).fetchone()[0]
        == 3
    )


def test_an_artefact_whose_manifest_lost_a_field_writes_nothing(
    rollback: Any, snapshot: RawStore, network_guard
) -> None:
    """The raw store is not in the database backup, so damage to it is a case.

    The refusal has to happen before any row is written — a record inserted
    from a manifest that no longer says where the bytes came from is exactly
    the row the provenance view exists to make impossible.
    """
    network_guard(allow=("psycopg",))
    load_source(rollback, snapshot, SOURCE)
    before = counts(rollback)
    raw_path = _strip_a_field(snapshot, "source_version")

    with pytest.raises(MalformedManifestError):
        load_artefact(rollback, snapshot, ReferenceNormaliser(), raw_path)

    assert counts(rollback) == before


def _strip_a_field(store: RawStore, field: str) -> str:
    """Remove one key from a stored manifest, and say which record it was."""
    raw_path = store.scan(SOURCE)[0]
    directory = store.root / raw_path
    manifest = directory / MANIFEST_NAME
    directory.chmod(0o700)
    manifest.chmod(0o600)
    stored = json.loads(manifest.read_text(encoding="utf-8"))
    del stored[field]
    manifest.write_text(json.dumps(stored), encoding="utf-8")
    return raw_path


# --- the positive control --------------------------------------------------


def test_the_guard_is_live_while_the_database_still_answers(
    rollback: Any, network_guard, network_reached: type[AssertionError]
) -> None:
    """Both halves of the exemption, asserted rather than assumed.

    Without the first half this file's guard could be inert and every test
    above would still pass; without the second, the exemption could be doing
    nothing and the tests would fail for an unrelated reason.
    """
    import httpx

    network_guard(allow=("psycopg",))

    with pytest.raises(network_reached, match=r"httpx\.Client\.send"):
        httpx.Client().get("https://reference-archive.invalid/v1/")
    assert rollback.execute("select 1").fetchone()[0] == 1
