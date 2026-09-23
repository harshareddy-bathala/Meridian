"""Stage 14's completion gate: downloaded once, then normalised with no network.

*An archive snapshot can be downloaded once, then repeatedly normalised and
evaluated without network access.*

Two things make this a proof rather than a restatement of the sentence.

**The fixtures are deleted after the snapshot is published.** Everything below
runs against a raw store whose source no longer exists anywhere on the machine,
so anything reaching back past it fails with a missing file rather than passing
for the wrong reason.

**The normalisation runs under ``network_guard``**, three times, the last
through ``cli.main`` — the command an operator actually types. Byte-identical
canonical output each time, so "repeatedly" means the same answer and not
merely the same absence of an exception.

**The guard has a positive control**, following
``test_import_boundaries.py::test_the_scan_would_notice_a_crossing``, and so
does the deletion. A gate that passes because its guard is inert is the worst
failure available here — it looks done.

Reference: docs/SOFTWARE-IMPLEMENTATION-ROADMAP.md Stage 14; docs/DECISIONS.md
D-141, D-142.
"""

from __future__ import annotations

import hashlib
import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from meridian_ingest.adapters.protocol import FetchRequest
from meridian_ingest.adapters.reference import (
    FIXTURE_ROOT,
    ReferenceAdapter,
    ReferenceNormaliser,
)
from meridian_ingest.cli import main
from meridian_ingest.provenance import Provenance
from meridian_ingest.raw_manifest import canonical_bytes
from meridian_ingest.raw_store import RawStore
from meridian_ingest.retrieval import FixtureRetriever, RetrievalError

SOURCE = "reference_archive"
RETRIEVED_AT = datetime(2026, 9, 20, 10, 11, 43, tzinfo=UTC)
"""Fixed, so the published directory names are the same on every run.

They embed the retrieval instant, and a gate whose evidence is named after the
clock cannot be compared with the same gate run yesterday.
"""


@dataclass(frozen=True)
class Snapshot:
    """One downloaded archive, and the fixtures it came from — now deleted."""

    store: RawStore
    config: Path
    fixtures: Path


@pytest.fixture
def snapshot(tmp_path: Path) -> Iterator[Snapshot]:
    """Download the reference archive once, then remove the source.

    The download is the real path: the adapter plans the https URLs it would
    plan against any archive, a retriever turns each into bytes, and the raw
    store publishes them. Only the retriever is fixture-backed, which is the
    seam that exists for exactly this.
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

    config = tmp_path / "ingest.toml"
    config.write_text('raw_root = "raw"\n', encoding="utf-8")
    yield Snapshot(store=store, config=config, fixtures=fixtures)

    for path in sorted(store.root.rglob("*"), reverse=True):
        path.chmod(0o700 if path.is_dir() else 0o600)


def normalised(store: RawStore) -> dict[str, object]:
    """Everything normalising this snapshot produces, as JSON-native values.

    Digests rather than fields: the record types already canonicalise
    themselves into ``content_sha256``, and that is the value the archive tables
    key on — so this compares what would be *stored*, not a restatement of it.
    """
    normaliser = ReferenceNormaliser()
    produced: dict[str, object] = {}
    for raw_path in store.scan(SOURCE):
        artefact = store.read(raw_path)
        if artefact.manifest.provenance.payload_kind == "tile":
            produced[raw_path] = "tile, nothing derived"
            continue
        batch = normaliser.normalise(artefact)
        produced[raw_path] = {
            "transformation_version": batch.transformation_version,
            "stations": [one.content_sha256.hex() for one in batch.stations],
            "receptions": [one.content_sha256.hex() for one in batch.receptions],
        }
    return produced


def reception_digests(produced: dict[str, object]) -> list[str]:
    """Every reception digest in one normalisation, in artefact order."""
    found: list[str] = []
    for value in produced.values():
        if isinstance(value, dict):
            found.extend(value["receptions"])
    return found


# --- the gate --------------------------------------------------------------


def test_a_snapshot_normalises_identically_three_times_with_nothing_reachable(
    snapshot: Snapshot,
    network_guard,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The completion gate, run rather than asserted.

    The third pass is ``meridian-ingest normalise`` itself, because the claim
    is about the command an operator has, not about the functions underneath
    it.
    """
    network_guard()

    first = canonical_bytes(normalised(snapshot.store))
    second = canonical_bytes(normalised(snapshot.store))
    capsys.readouterr()
    exit_code = main(["--config", str(snapshot.config), "normalise"])
    printed = capsys.readouterr().out

    assert first == second
    assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()
    assert exit_code == 0
    for digest in reception_digests(normalised(snapshot.store)):
        assert digest[:16] in printed, "the command printed the same digests"


def test_the_snapshot_holds_everything_the_gate_needs(snapshot: Snapshot) -> None:
    """Three artefacts and seven receptions, so the gate is not passing on nothing.

    A gate over an empty store would satisfy every other assertion in this file
    without normalising a single byte.
    """
    produced = normalised(snapshot.store)

    assert len(snapshot.store.scan(SOURCE)) == 3
    assert len(reception_digests(produced)) == 7
    assert "tile, nothing derived" in produced.values()


def test_verify_re_hashes_the_snapshot_with_nothing_reachable(
    snapshot: Snapshot, network_guard
) -> None:
    """The other offline verb: an operator checks the tree without a source."""
    network_guard()

    assert main(["--config", str(snapshot.config), "verify"]) == 0


# --- the positive control for the deletion ---------------------------------


def test_the_fixtures_really_are_gone(snapshot: Snapshot) -> None:
    """Without this, "normalisation reads only the raw store" is untested.

    The fixtures still being on disk is the way this whole file passes while
    proving nothing, so the deletion is asserted rather than trusted — and
    asserted by trying to retrieve from it, which is what a normaliser reaching
    backwards would end up doing.
    """
    adapter = ReferenceAdapter()
    remote = adapter.plan(FetchRequest())[0]

    assert not snapshot.fixtures.exists()
    with pytest.raises(RetrievalError, match="has no fixture at"):
        FixtureRetriever(snapshot.fixtures).retrieve(remote)


# --- the positive control for the guard ------------------------------------


def test_the_guard_would_notice_a_connection(
    network_guard, network_reached: type[AssertionError]
) -> None:
    """A guard that never fires cannot distinguish a gate from a wish."""
    network_guard()

    with pytest.raises(network_reached, match=r"socket\.socket\.connect"):
        _connect()


def test_the_guard_catches_a_socket_class_bound_before_it_was_installed(
    network_guard, network_reached: type[AssertionError]
) -> None:
    """Which is why it patches ``socket.socket`` and not a module binding.

    A module that did ``from socket import socket`` at import time holds the
    class, not the name — rebinding the name in that module would leave it
    with the real class, and the guard would be inert exactly where it matters.
    """
    from socket import socket as bound_early

    network_guard()

    with pytest.raises(network_reached, match=r"socket\.socket\.connect"):
        bound_early().connect(("127.0.0.1", 9))


def test_the_guard_names_the_layer_that_reached(
    network_guard, network_reached: type[AssertionError]
) -> None:
    """httpx is patched above the socket that would have caught it anyway.

    Only so a failure says ``httpx.Client.send`` rather than a connect from
    inside somebody else's connection pool.
    """
    import httpx

    network_guard()

    with pytest.raises(network_reached, match=r"httpx\.Client\.send"):
        httpx.Client().get("https://reference-archive.invalid/v1/")


def test_a_layer_can_be_left_alone(
    network_guard, network_reached: type[AssertionError]
) -> None:
    """The integration half of the gate needs its own database connection."""
    import psycopg

    network_guard(allow=("psycopg",))

    assert psycopg.connect is not None
    with pytest.raises(network_reached, match=r"socket\.socket\.connect"):
        _connect()


def test_the_guard_refuses_a_layer_it_does_not_have(network_guard) -> None:
    """A typo in ``allow`` would otherwise exempt nothing, silently and safely.

    Safely is the problem: the test would pass, and the exemption it asked for
    would never have been applied.
    """
    with pytest.raises(ValueError, match="no network layer named"):
        network_guard(allow=("sockets",))


def test_the_guard_cannot_see_inside_libpq(network_guard) -> None:
    """psycopg[binary] reaches a network where no Python patch can watch it.

    libpq opens its own sockets in C. Asserted rather than assumed, because the
    assumption in the other direction — that the socket guard covers the
    database too — is how a guard comes to be trusted for something it never
    did. The integration half of the gate therefore claims only that *Python*
    reached nothing, which is the claim it can support.
    """
    import psycopg

    network_guard(allow=("psycopg",))

    with pytest.raises(psycopg.OperationalError):
        psycopg.connect("postgresql://nobody@127.0.0.1:1/nothing", connect_timeout=2)


def _connect() -> None:
    """One connection attempt to a port nothing listens on."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.connect(("127.0.0.1", 9))
