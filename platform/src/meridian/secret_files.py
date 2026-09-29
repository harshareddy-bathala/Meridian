"""Secrets read from a file named by ``<NAME>_FILE``, or from ``<NAME>``.

Split from :mod:`meridian.config`, which had reached its size limit, so the
reading rules live in one module of their own. Settings are read once, at
start-up, so a rotated secret takes effect when the process restarts (D-201).

``InsecureConfigurationError`` is defined here rather than in ``config`` only
because ``config`` imports this module; ``meridian.config`` re-exports it, and
that is the name the rest of the platform uses.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = [
    "PLACEHOLDER",
    "InsecureConfigurationError",
    "read_previous_secret",
    "read_secret",
]

PLACEHOLDER = "change-me"
"""The development value every secret defaults to, refused on a public address."""


class InsecureConfigurationError(RuntimeError):
    """Raised when a placeholder secret would be exposed publicly."""


_FILE_SUFFIX = "_FILE"


def read_secret(name: str, default: str) -> str:
    """Read a secret from ``<name>_FILE`` when that is set, else from ``<name>``.

    The file form keeps a secret out of the process environment, where
    ``docker inspect`` and ``/proc/<pid>/environ`` show it to anyone on the host
    (D-114). The file wins when both are set, so mounting one is enough to
    override a value left behind in ``.env``.

    Surrounding whitespace is removed, because ``openssl rand -hex 32 > file``
    ends the file with a newline that is not part of the secret.

    Raises:
        InsecureConfigurationError: The named file cannot be read, or is empty.
            Falling back to the variable would start the platform on a secret
            the operator believes they replaced.
    """
    path = os.environ.get(name + _FILE_SUFFIX, "").strip()
    if not path:
        return os.environ.get(name, default)
    try:
        value = Path(path).read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise InsecureConfigurationError(
            f"{name}{_FILE_SUFFIX} names {path!r}, which cannot be read: {exc.strerror}"
        ) from exc
    if not value:
        raise InsecureConfigurationError(f"{name}{_FILE_SUFFIX} names an empty file")
    return value


def read_previous_secret(name: str) -> str:
    """Read a secret being rotated away from; empty means there is none (D-201).

    The one exception to :func:`read_secret`'s refusal of an empty file. That
    refusal stops the platform starting on a placeholder the operator believes
    they replaced; an empty *previous* secret accepts fewer credentials, not
    more, so it has nothing to protect and is how a rotation is retired. A named
    file that cannot be read is still refused.
    """
    path = os.environ.get(name + _FILE_SUFFIX, "").strip()
    if not path:
        return os.environ.get(name, "").strip()
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise InsecureConfigurationError(
            f"{name}{_FILE_SUFFIX} names {path!r}, which cannot be read: {exc.strerror}"
        ) from exc
