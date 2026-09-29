"""A logging filter that removes credentials and secrets from every log line (D-204).

The platform's error bodies never carry a secret (D-004); its logs could. A
validation failure logs the rejected input, which on ``register`` is an invite
token and a registration key. An exception's text can carry a connection string
with its password. A future log line could carry anything.

So the filter is on the **handler**, not on any one logger: every record the
platform writes passes through it, whichever module logged it, including
uvicorn's and the libraries'. It removes three things:

- anything after ``Bearer``;
- the value of any field whose name says it holds a secret — a token, a
  registration key, a password, a pepper, a secret — in the ``name=value``,
  ``name: value`` and ``'name': 'value'`` spellings logs and reprs use;
- the password in a URL's ``user:password@``;

and, beyond the patterns, every secret value this process has loaded, wherever
it appears. :func:`meridian.config.load_settings` registers those, so a process
that reads a secret cannot log it verbatim, however it got into the line.

The message is formatted, redacted and stored back on the record, and so is its
traceback, so a formatter further down sees only the redacted text.

Reference: docs/DECISIONS.md D-004, D-114, D-204.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable

__all__ = [
    "REDACTED",
    "RedactingFilter",
    "forget_secrets",
    "redact",
    "remember_secrets",
]

REDACTED = "[redacted]"

MIN_SECRET_LENGTH = 16
"""Shorter values are not matched verbatim, only by the patterns.

The platform's secrets are 64 hex characters (``openssl rand -hex 32``). A short
value is more likely to be an ordinary word: the development database password
is ``meridian``, and replacing it everywhere would redact every path and name
that contains the project's own name while protecting nothing.
"""

_SECRET_NAME = r"[\w-]*(?:token|registration_key|password|passwd|pepper|secret)"

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # `Bearer abc…` in a header echoed into a message or a repr. Not the prose
    # "bearer token", which the platform's own lines use: redacting its second
    # word would mangle a line the runbook tells operators to look for.
    (re.compile(r"(?i)\b(bearer)\s+(?!tokens?\b)[^\s'\",;]+"), rf"\1 {REDACTED}"),
    # `invite_token='abc'`, `"registration_key": "abc"`, `METRICS_TOKEN=abc`.
    (
        re.compile(rf"(?i)({_SECRET_NAME}['\"]?\s*[:=]\s*b?['\"]?)([^'\"\s,;)}}\]]+)"),
        rf"\1{REDACTED}",
    ),
    # postgresql://user:password@host — any scheme, any user.
    (re.compile(r"(\b[a-z][a-z0-9+.-]*://[^:/@\s]*:)([^@\s]+)(@)"), rf"\1{REDACTED}\3"),
)

_known: set[str] = set()


def remember_secrets(values: Iterable[str]) -> None:
    """Redact these exact values from every later log line of this process."""
    _known.update(v for v in values if len(v) >= MIN_SECRET_LENGTH)


def forget_secrets() -> None:
    """Forget every remembered value; for tests, which load several settings."""
    _known.clear()


def redact(text: str) -> str:
    """``text`` with every secret this module recognises replaced."""
    # Longest first, so a secret containing another is replaced whole.
    for value in sorted(_known, key=len, reverse=True):
        text = text.replace(value, REDACTED)
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class RedactingFilter(logging.Filter):
    """Rewrites each record's message and traceback before it is written."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Redact ``record`` in place; never drops it."""
        try:
            message = record.getMessage()
        except Exception:
            # A malformed call (arguments that do not fit the format) would
            # otherwise raise out of Handler.handle into the code that logged,
            # where logging's own error handling never sees it. Keep both
            # halves, redacted, so the line still says what went wrong.
            message = f"{record.msg!s} {record.args!r}"
        record.msg = redact(message)
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        return True
