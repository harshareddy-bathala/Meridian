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


def test_the_words_bearer_token_are_prose_not_a_credential() -> None:
    """The rotation line the runbook tells operators to look for survives."""
    line = "bearer token re-hashed under the new pepper"
    assert redact(line) == line
    assert redact("Bearer tokenabc123") == f"Bearer {REDACTED}"


def test_a_malformed_logging_call_does_not_raise() -> None:
    record = logging.LogRecord(
        "meridian.test", logging.INFO, __file__, 1, "%s and %s", ("Bearer abc",), None
    )

    assert RedactingFilter().filter(record)
    assert "abc" not in record.getMessage().replace(REDACTED, "")


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


def test_the_planners_place_is_withheld_from_the_access_line() -> None:
    """The planner keeps nothing of a place, its log line included (D-370)."""
    line = '172.18.0.5:41234 - "GET /api/v1/plan?lat=12.3&lon=77.6 HTTP/1.1" 200'

    redacted = redact(line)

    assert redacted == f'172.18.0.5:41234 - "GET /api/v1/plan?{REDACTED} HTTP/1.1" 200'


def test_another_route_keeps_its_query() -> None:
    """Only the planner's query is a place; a cursor is worth having in a log."""
    line = '"GET /api/v1/passes?limit=50&station_id=st_1 HTTP/1.1" 200'
    assert redact(line) == line


def test_the_filter_withholds_the_place_from_uvicorns_own_record() -> None:
    """uvicorn passes the path as an argument; the filter formats it first."""
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("10.0.0.1:5", "GET", "/api/v1/plan?lat=-33.9&lon=151.2", "1.1", 200),
        None,
    )

    RedactingFilter().filter(record)

    assert "151.2" not in record.getMessage()
    assert "/api/v1/plan?" in record.getMessage()
