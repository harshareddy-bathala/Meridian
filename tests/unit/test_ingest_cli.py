"""``meridian-ingest`` — its verbs, its refusals, and its exit codes.

Every test here runs ``main`` in process against a raw store under ``tmp_path``.
Nothing reaches a network: the reference source declares its own retriever, so
``fetch`` reads the synthetic files shipped beside the adapter, and no other
verb has a retriever at all.

The exit codes are the interface a monitoring script uses, so they are asserted
as values rather than as "not zero" — particularly **3**, which has to mean
"the raw store is damaged" and nothing else.

Reference: docs/DECISIONS.md D-133, D-134, D-138, D-141, D-142.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from meridian.store.archive_observations import NormalisationDisagreementError
from meridian_ingest import cli
from meridian_ingest.adapters.reference import REFERENCE_SOURCE
from meridian_ingest.cli import EXIT_CORRUPT, EXIT_FAILED, EXIT_USAGE, main
from meridian_ingest.cli_fetch import check_attribution
from meridian_ingest.load import TermsChangedError
from meridian_ingest.normalise.records import NormalisationError
from meridian_ingest.raw_layout import ARTEFACT_NAME

SOURCE = "reference_archive"


@pytest.fixture
def config(tmp_path: Path) -> Path:
    """A settings file pointing the raw store at ``tmp_path``."""
    path = tmp_path / "ingest.toml"
    path.write_text('raw_root = "raw"\n', encoding="utf-8")
    return path


@pytest.fixture
def raw(tmp_path: Path) -> Path:
    """Where that settings file puts the raw store, left writable for teardown."""
    root = tmp_path / "raw"
    yield root
    for path in sorted(root.rglob("*"), reverse=True):
        path.chmod(0o700 if path.is_dir() else 0o600)


def run(config: Path, *args: str) -> int:
    return main(["--config", str(config), *args])


def records(raw: Path) -> list[Path]:
    """The published directories, which is not everything under a source.

    ``.incoming`` lives there too and is never a record — counting it would
    make a crashed run look like a retrieval.
    """
    return [one for one in (raw / SOURCE).iterdir() if not one.name.startswith(".")]


# --- the command tree ------------------------------------------------------


def test_a_bare_invocation_names_its_verbs_and_touches_nothing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Exit 0 with help: the caller asked for something incomplete, not broken."""
    assert main([]) == 0

    printed = capsys.readouterr().out
    for verb in ("sources", "fetch", "normalise", "load", "verify"):
        assert verb in printed


def test_the_version_is_the_distributions(capsys: pytest.CaptureFixture[str]) -> None:
    from meridian_ingest import __version__

    with pytest.raises(SystemExit) as raised:
        main(["--version"])

    assert raised.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_an_unregistered_source_is_refused_as_a_sentence(
    config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Not wrapped in quotation marks, which is what a bare KeyError prints."""
    assert run(config, "sources", "--source", "nope") == EXIT_FAILED

    said = capsys.readouterr().err
    assert "no source is registered as 'nope'" in said
    assert f"registered: {SOURCE}" in said


def test_a_settings_file_that_cannot_be_obeyed_stops_everything(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = tmp_path / "ingest.toml"
    broken.write_text("nonsense = 1\n", encoding="utf-8")

    assert main(["--config", str(broken), "sources"]) == EXIT_FAILED
    assert "unknown key" in capsys.readouterr().err


# --- sources ---------------------------------------------------------------


def test_sources_prints_the_terms_rather_than_the_counts(
    config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The question it answers is "may we take this, and did we say so"."""
    assert run(config, "sources") == 0

    printed = capsys.readouterr().out
    assert REFERENCE_SOURCE.licence in printed
    assert REFERENCE_SOURCE.terms_url in printed
    assert REFERENCE_SOURCE.attribution_entry in printed


def test_sources_lists_a_disabled_source_too(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A source switched off still has terms, and hiding it answers wrongly."""
    path = tmp_path / "ingest.toml"
    path.write_text(f"[sources.{SOURCE}]\nenabled = false\n", encoding="utf-8")

    assert main(["--config", str(path), "sources"]) == 0
    assert "disabled" in capsys.readouterr().out


# --- fetch -----------------------------------------------------------------


def test_fetch_publishes_every_planned_artefact(
    config: Path, raw: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(config, "fetch") == 0

    printed = capsys.readouterr().out
    assert printed.count("retrieved") == 3
    assert len(records(raw)) == 3


def test_a_second_fetch_leaves_the_first_artefacts_untouched(
    config: Path, raw: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Whether it writes new directories depends on the clock; immutability does not.

    Two fetches a second apart are two retrievals and two directories; within
    one second they are the same directory and the rename refuses the second.
    Either way nothing already published changes, which is the part worth
    asserting here — the naming rule itself is pinned in
    ``test_ingest_raw_store.py``, where the clock is not involved.
    """
    run(config, "fetch")
    before = {one.name: _digest(one) for one in records(raw)}
    capsys.readouterr()

    assert run(config, "fetch") == 0

    after = {one.name: _digest(one) for one in records(raw)}
    assert all(after[name] == digest for name, digest in before.items())
    assert run(config, "verify") == 0


def _digest(record: Path) -> bytes:
    import hashlib

    return hashlib.sha256((record / ARTEFACT_NAME).read_bytes()).digest()


def test_a_bound_without_an_offset_is_a_usage_error(config: Path) -> None:
    """An operator outside UTC would otherwise fetch a different month, quietly."""
    with pytest.raises(SystemExit) as raised:
        run(config, "fetch", "--since", "2026-08-01T00:00:00")

    assert raised.value.code == EXIT_USAGE


def test_a_bound_with_an_offset_is_accepted(config: Path, raw: Path) -> None:
    assert run(config, "fetch", "--since", "2026-08-01T00:00:00Z") == 0
    assert len(records(raw)) == 2, "August's file and August's tile, not July's"


# --- the attribution check, which happens before any socket ----------------


def test_a_source_credited_where_it_says_it_is_passes() -> None:
    check = check_attribution(REFERENCE_SOURCE, Path(__file__).resolve().parent)

    assert check.file is not None
    assert check.entry_found is True
    assert check.refuses is False


def test_a_source_the_file_does_not_mention_is_refused(tmp_path: Path) -> None:
    """D-134: credited before the first retrieval, not after it."""
    (tmp_path / "ATTRIBUTION.md").write_text("# Nothing here\n", encoding="utf-8")

    check = check_attribution(REFERENCE_SOURCE, tmp_path)

    assert check.refuses is True


def test_no_attribution_file_at_all_does_not_refuse(tmp_path: Path) -> None:
    """An installation outside a checkout has nothing to check against.

    Saying so out loud is right; refusing would make the command unusable for a
    reason that has nothing to do with the terms, and the obligation is
    enforced by a test over every registered source anyway.
    """
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)

    check = check_attribution(REFERENCE_SOURCE, deep)

    assert check.file is None
    assert check.refuses is False


# --- normalise, the offline half of the gate -------------------------------


@pytest.mark.usefixtures("raw")
def test_normalise_writes_nothing_and_prints_a_digest_per_reception(
    config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run(config, "fetch")
    capsys.readouterr()

    assert run(config, "normalise") == 0

    printed = capsys.readouterr().out
    assert "7 receptions" not in printed, "it reports per artefact, not in total"
    assert printed.count("version reference-1") == 2
    assert "tile, nothing derived" in printed


@pytest.mark.usefixtures("raw")
def test_normalising_twice_prints_the_same_bytes(
    config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The completion gate at a shell prompt: run it twice and ``diff`` it."""
    run(config, "fetch")
    capsys.readouterr()

    run(config, "normalise")
    first = capsys.readouterr().out
    run(config, "normalise")
    second = capsys.readouterr().out

    assert first == second
    assert first.strip(), "and it printed something to compare"


# --- verify, and the exit code that belongs to it --------------------------


@pytest.mark.usefixtures("raw")
def test_verify_accepts_an_intact_tree(
    config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run(config, "fetch")
    capsys.readouterr()

    assert run(config, "verify") == 0
    assert "3 artefacts match" in capsys.readouterr().out


def test_a_damaged_artefact_exits_three_and_names_itself(
    config: Path, raw: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Three, not one: a monitoring script has to tell these apart.

    "verify could not run" and "the one thing that cannot be recreated without
    going back to the source is damaged" call for different people.
    """
    run(config, "fetch")
    capsys.readouterr()
    damaged = next(raw.rglob(ARTEFACT_NAME))
    damaged.chmod(0o600)
    damaged.write_bytes(damaged.read_bytes() + b"x")

    assert run(config, "verify") == EXIT_CORRUPT

    said = capsys.readouterr().err
    assert damaged.parent.name in said
    assert "not in the database backup" in said


# --- load ------------------------------------------------------------------


@pytest.mark.usefixtures("raw")
def test_load_without_a_database_fails_cleanly(
    config: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Exit 1 and a sentence, as every other command in the project does.

    Port 1 is reserved and nothing listens on it — the address the platform's
    own CLI tests use for the same reason.
    """
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql://meridian:meridian@127.0.0.1:1/meridian"
    )
    run(config, "fetch")
    capsys.readouterr()

    assert run(config, "load") == EXIT_FAILED

    said = capsys.readouterr().err
    assert "cannot reach the database" in said
    assert "Traceback" not in said


class _Connection:
    """Stands in for ``connect_once``'s connection: records how it was set up."""

    def __init__(self) -> None:
        self.autocommit = False

    def __enter__(self) -> _Connection:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


@pytest.mark.usefixtures("raw")
def test_load_commits_each_artefact_on_its_own(
    config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Autocommit, so ``load_artefact``'s transaction is real and not a savepoint.

    On a connection already inside a transaction, a failure in the last
    artefact would roll back every one that had finished.
    """
    connection = _Connection()
    seen: list[bool] = []

    class _SeenError(Exception):
        pass

    def record(conn: _Connection, *_args: object) -> object:
        seen.append(conn.autocommit)
        raise _SeenError

    monkeypatch.setattr(cli, "connect_once", lambda _settings: connection)
    monkeypatch.setattr(cli, "load_source", record)

    with pytest.raises(_SeenError):
        run(config, "load")

    assert seen == [True]


@pytest.mark.usefixtures("raw")
@pytest.mark.parametrize(
    "error",
    [
        TermsChangedError("reference_archive is already registered under other terms"),
        NormalisationError("a reception with no start"),
        NormalisationDisagreementError("one key, two bodies"),
    ],
    ids=["terms-changed", "normalisation", "disagreement"],
)
def test_a_load_that_is_refused_says_why_without_a_traceback(
    error: Exception,
    config: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Exit 1 and the error's own sentence, like every other refusal here."""

    def refuse(*_args: object) -> object:
        raise error

    monkeypatch.setattr(cli, "connect_once", lambda _settings: _Connection())
    monkeypatch.setattr(cli, "load_source", refuse)

    assert run(config, "load") == EXIT_FAILED

    said = capsys.readouterr().err
    assert str(error) in said
    assert "Traceback" not in said
