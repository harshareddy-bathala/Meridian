"""The settings file: what it refuses, and what it does without one.

Two properties carry most of this. **Strict**, because a misspelled key that was
ignored is a limit somebody believes they set — and the only cheap moment to
find out otherwise is when the file is read. **Optional**, because the whole
point of shipping a fixture-backed source is that the completion gate runs for
somebody who has installed the distribution and configured nothing.

The third is narrower and the most valuable: ``api_key_env`` holds the *name* of
a variable, and a key pasted there is refused by name with instructions to
rotate it. That mistake ends with a secret in a file that gets committed.

Reference: docs/DECISIONS.md D-114, D-134, D-141.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from meridian_ingest.config import (
    CONFIG_ENV,
    DEFAULT_RAW_ROOT,
    ConfigurationError,
    SourceSettings,
    find_config,
    load_settings,
)
from meridian_ingest.credentials import MissingKeyError, read_api_key

SOURCE = "reference_archive"


def written(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "ingest.toml"
    path.write_text(body, encoding="utf-8")
    return path


# --- running on nothing at all ---------------------------------------------


def test_with_no_file_the_defaults_fetch_the_reference_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Somebody who installed this and configured nothing can still run the gate."""
    monkeypatch.chdir(tmp_path)

    settings = load_settings(path=None, environ={})

    assert settings.raw_root == DEFAULT_RAW_ROOT
    assert settings.enabled_sources() == (SOURCE,)
    assert settings.for_source(SOURCE).request_budget == 50


def test_a_source_the_file_does_not_mention_gets_the_defaults(tmp_path: Path) -> None:
    """Absent means default, not disabled: the file is for departing from them."""
    settings = load_settings(written(tmp_path, "timeout_s = 5.0\n"))

    assert settings.for_source(SOURCE) == SourceSettings(source_id=SOURCE)


def test_an_empty_file_is_the_same_as_no_file(tmp_path: Path) -> None:
    settings = load_settings(written(tmp_path, ""))

    assert settings.enabled_sources() == (SOURCE,)


# --- finding the file ------------------------------------------------------


def test_the_named_file_is_used(tmp_path: Path) -> None:
    path = written(tmp_path, "timeout_s = 9.0\n")

    assert find_config({CONFIG_ENV: str(path)}) == path


def test_a_named_file_that_is_not_there_is_an_error_not_a_fallback() -> None:
    """Otherwise the run uses defaults the operator believes they replaced."""
    with pytest.raises(ConfigurationError, match="not a readable file"):
        find_config({CONFIG_ENV: "/nowhere/ingest.toml"})


def test_no_variable_and_no_file_beside_us_is_no_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    assert find_config({}) is None


def test_a_file_beside_us_is_found_without_being_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    written(tmp_path, "timeout_s = 9.0\n")
    monkeypatch.chdir(tmp_path)

    assert find_config({}) == Path("ingest.toml")


# --- paths -----------------------------------------------------------------


def test_a_relative_root_resolves_against_the_file_not_the_working_directory(
    tmp_path: Path,
) -> None:
    """So the line means the same tree whichever directory the command is run from."""
    path = written(tmp_path, 'raw_root = "archive/raw"\n')

    assert load_settings(path).raw_root == (tmp_path / "archive/raw").resolve()


def test_an_absolute_root_is_left_alone(tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    path = written(tmp_path, f'raw_root = "{elsewhere}"\n')

    assert load_settings(path).raw_root == elsewhere.resolve()


@pytest.mark.parametrize("value", ['""', '"   "', "7", "true"])
def test_a_root_that_is_not_a_path_is_refused(tmp_path: Path, value: str) -> None:
    with pytest.raises(ConfigurationError, match="raw_root"):
        load_settings(written(tmp_path, f"raw_root = {value}\n"))


# --- strictness ------------------------------------------------------------


def test_an_unknown_top_level_key_is_refused(tmp_path: Path) -> None:
    """A misspelling that was ignored is a setting somebody believes they made."""
    with pytest.raises(ConfigurationError, match="unknown key"):
        load_settings(written(tmp_path, 'raw_rooot = "x"\n'))


def test_the_refusal_names_the_keys_that_do_exist(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="known: contact, raw_root"):
        load_settings(written(tmp_path, "nonsense = 1\n"))


def test_an_unknown_retry_key_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match=r"\[retry\]"):
        load_settings(written(tmp_path, "[retry]\nbackoff = 2\n"))


def test_an_unknown_source_key_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="budget"):
        load_settings(written(tmp_path, f"[sources.{SOURCE}]\nbudget = 2\n"))


def test_a_source_nothing_is_registered_for_is_refused(tmp_path: Path) -> None:
    """A typo'd id would otherwise be settings that do nothing, silently."""
    with pytest.raises(ConfigurationError, match="registered: reference_archive"):
        load_settings(written(tmp_path, "[sources.recption_archive]\nenabled = true\n"))


def test_bytes_that_are_not_toml_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="could not be read as TOML"):
        load_settings(written(tmp_path, "raw_root = [unclosed\n"))


@pytest.mark.parametrize(
    "body",
    [
        "timeout_s = 0\n",
        "timeout_s = -1\n",
        'timeout_s = "quick"\n',
        "[retry]\nattempts = 0\n",
        "[retry]\nattempts = true\n",
        "[retry]\nbase_delay_s = 60.0\nmax_delay_s = 1.0\n",
        f"[sources.{SOURCE}]\nrequest_budget = 0\n",
        f"[sources.{SOURCE}]\nenabled = 1\n",
    ],
)
def test_a_value_that_could_not_be_obeyed_is_refused(tmp_path: Path, body: str) -> None:
    with pytest.raises(ConfigurationError):
        load_settings(written(tmp_path, body))


def test_a_mirror_over_plain_http_is_refused(tmp_path: Path) -> None:
    """Over http an intermediary chooses what our digest records as evidence."""
    with pytest.raises(ConfigurationError, match="https"):
        load_settings(
            written(tmp_path, f'[sources.{SOURCE}]\nbase_url = "http://m.invalid/"\n')
        )


# --- what the file is allowed to say ---------------------------------------


def test_the_settings_are_read_as_written(tmp_path: Path) -> None:
    path = written(
        tmp_path,
        'raw_root = "raw"\n'
        "timeout_s = 12.5\n"
        'contact = "someone@example.org"\n'
        "[retry]\nattempts = 2\nbase_delay_s = 0.5\nmax_delay_s = 4.0\n"
        f"[sources.{SOURCE}]\nenabled = false\nrequest_budget = 7\n"
        'base_url = "https://mirror.invalid/v1/"\n',
    )

    settings = load_settings(path)

    assert settings.timeout_s == 12.5
    assert settings.contact == "someone@example.org"
    assert settings.retry.attempts == 2
    assert settings.retry.max_delay_s == 4.0
    assert settings.for_source(SOURCE).request_budget == 7
    assert settings.for_source(SOURCE).base_url == "https://mirror.invalid/v1/"


def test_a_disabled_source_keeps_its_table_and_leaves_the_fetch(
    tmp_path: Path,
) -> None:
    """So the terms it was registered under stay written down."""
    path = written(tmp_path, f"[sources.{SOURCE}]\nenabled = false\n")

    settings = load_settings(path)

    assert settings.enabled_sources() == ()
    assert settings.for_source(SOURCE).enabled is False


# --- keys ------------------------------------------------------------------


def test_a_source_that_needs_no_key_asks_for_none() -> None:
    assert read_api_key(SOURCE, None, {}) is None


def test_a_key_is_read_from_the_variable_the_file_named() -> None:
    key = read_api_key(SOURCE, "ARCHIVE_KEY", {"ARCHIVE_KEY": " s3cret \n"})

    assert key == "s3cret"


def test_a_key_file_wins_over_the_variable(tmp_path: Path) -> None:
    """D-114: a file keeps the value out of /proc/<pid>/environ."""
    holding = tmp_path / "key"
    holding.write_text("from-the-file\n", encoding="utf-8")

    key = read_api_key(
        SOURCE,
        "ARCHIVE_KEY",
        {"ARCHIVE_KEY": "from-the-env", "ARCHIVE_KEY_FILE": str(holding)},
    )

    assert key == "from-the-file"


def test_an_unset_key_stops_before_the_fetch() -> None:
    """Otherwise the source's request budget is spent on a wall of 401s."""
    with pytest.raises(MissingKeyError, match="which is unset"):
        read_api_key(SOURCE, "ARCHIVE_KEY", {})


def test_an_empty_key_file_is_refused(tmp_path: Path) -> None:
    holding = tmp_path / "key"
    holding.write_text("\n", encoding="utf-8")

    with pytest.raises(MissingKeyError, match="which is empty"):
        read_api_key(SOURCE, "ARCHIVE_KEY", {"ARCHIVE_KEY_FILE": str(holding)})


@pytest.mark.parametrize(
    "pasted",
    ["sk-live-8f3a9c2b1d", "lowercase_name", "Has Spaces", "a/b", "1LEADING_DIGIT"],
)
def test_a_key_pasted_where_its_name_belongs_is_refused(
    tmp_path: Path, pasted: str
) -> None:
    """The mistake that ends with a secret in a committed file.

    We cannot stop the commit; we can refuse to run, say which line, and say to
    rotate it.
    """
    body = f'[sources.{SOURCE}]\napi_key_env = "{pasted}"\n'

    with pytest.raises(ConfigurationError, match="rotate it"):
        load_settings(written(tmp_path, body))


def test_a_variable_name_is_accepted(tmp_path: Path) -> None:
    path = written(tmp_path, f'[sources.{SOURCE}]\napi_key_env = "ARCHIVE_KEY"\n')

    assert load_settings(path).for_source(SOURCE).api_key_env == "ARCHIVE_KEY"


def test_a_key_that_was_read_is_still_nowhere_in_the_settings() -> None:
    """The whole point of the indirection: settings get printed, and this does not."""
    settings = SourceSettings(source_id=SOURCE, api_key_env="ARCHIVE_KEY")

    key = read_api_key(SOURCE, settings.api_key_env, {"ARCHIVE_KEY": "s3cret"})

    assert key == "s3cret"
    assert "s3cret" not in repr(settings)
    assert "ARCHIVE_KEY" in repr(settings), "the name is not a secret, and is useful"
    holders = {field.name for field in dataclasses.fields(SourceSettings)}
    assert holders & {"api_key", "key", "secret", "token"} == set()


# --- the example ships honest ----------------------------------------------


def test_the_shipped_example_parses_under_the_strict_reader() -> None:
    """An example that does not load is documentation for a version we do not have."""
    example = Path(__file__).resolve().parents[2] / "deploy/ingest.toml.example"

    settings = load_settings(example)

    assert settings.enabled_sources() == (SOURCE,)
    assert settings.for_source(SOURCE).request_budget == 50


def test_the_shipped_example_contains_no_key() -> None:
    example = Path(__file__).resolve().parents[2] / "deploy/ingest.toml.example"
    text = example.read_text(encoding="utf-8")

    assert "api_key_env" in text, "it documents the indirection"
    assert 'api_key_env = "SOME_ARCHIVE_KEY"' in text, "and names a variable, not a key"
