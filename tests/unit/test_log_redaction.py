"""The redaction patterns of D-204, one line at a time.

``tests/integration/test_log_redaction.py`` drives the platform and searches
what it logged; this pins what each pattern removes and what it leaves.
"""

from __future__ import annotations

import logging

import pytest

from meridian.log_redaction import (
    REDACTED,
    RedactingFilter,
    forget_secrets,
    redact,
    remember_secrets,
)


@pytest.fixture(autouse=True)
def _forget() -> None:
    forget_secrets()


@pytest.mark.parametrize(
    ("line", "kept"),
    [
        ("Authorization: Bearer abc.def-123", "Authorization: Bearer"),
        ("{'invite_token': 'abc', 'name': 'n'}", "'name': 'n'"),
        ('{"registration_key": "abc"}', '"registration_key": "'),
        ("METRICS_TOKEN=abc123 and more", "and more"),
        ("bearer_token=abc)", "bearer_token="),
        ("postgresql://meridian:hunter2@db:5432/meridian", "@db:5432/meridian"),
        ("password: 'hunter2'", "password: '"),
    ],
)
def test_each_pattern_removes_the_value_and_keeps_the_rest(
    line: str, kept: str
) -> None:
    redacted = redact(line)

    assert kept in redacted
    assert REDACTED in redacted
    for value in ("abc", "hunter2", "abc123"):
        assert f"{value}" not in redacted.replace(REDACTED, "")


def test_ordinary_lines_are_left_alone() -> None:
    line = "station st_7fa3c1: 3 tokens left, rate limited for 10 s"
    assert redact(line) == line


def test_a_remembered_value_is_removed_wherever_it_appears() -> None:
    remember_secrets(["0f9a0f9a0f9a0f9a0f9a0f9a0f9a0f9a"])

    assert (
        redact("pepper is0f9a0f9a0f9a0f9a0f9a0f9a0f9a0f9a!") == f"pepper is{REDACTED}!"
    )


def test_a_short_value_is_not_remembered() -> None:
    """The development password `meridian` would redact the project's own name."""
    remember_secrets(["meridian"])

    assert redact("meridian.api started") == "meridian.api started"


def test_the_filter_redacts_the_arguments_and_the_traceback() -> None:
    record = logging.LogRecord(
        "meridian.test",
        logging.ERROR,
        __file__,
        1,
        "input was %s",
        ("Bearer abc",),
        None,
    )
    error = ValueError("registration_key='hunter2'")
    record.exc_info = (ValueError, error, None)

    assert RedactingFilter().filter(record) is True
    assert record.getMessage() == f"input was Bearer {REDACTED}"
    assert record.exc_text is not None
    assert "hunter2" not in record.exc_text
    assert "hunter2" not in logging.Formatter().format(record)
