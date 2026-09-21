"""Where the command's output goes.

Three functions, so that every verb writes the same way and the ``print``
exemption is declared in one place rather than at each call site. This is a
CLI: stdout is the interface for what happened, stderr for what went wrong.
"""

from __future__ import annotations

import sys

__all__ = ["EXIT_FAILED", "refuse", "say", "warn"]

EXIT_FAILED = 1


def say(line: str) -> None:
    """One line of output."""
    print(line)  # noqa: T201 — this is a CLI; stdout is the interface


def warn(line: str) -> None:
    """One line of bad news."""
    print(line, file=sys.stderr)  # noqa: T201 — this is a CLI; stderr is too


def refuse(line: str) -> int:
    """Say why, and give the exit code for having done nothing."""
    warn(line)
    return EXIT_FAILED
