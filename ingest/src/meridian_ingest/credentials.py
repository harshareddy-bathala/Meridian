"""Where a source's key comes from, and why it is never in the settings file.

Some archives are ``access_constraint = 'key_counted'``: they issue a key and
count what it does. The settings file names the **environment variable** that
holds one; this module reads it, at the moment a fetch needs it.

**The key is never a field on a settings object.** Settings get logged, printed
in a traceback, and dumped into a bug report; a value that only exists for the
length of one call does not. The indirection costs one line of configuration
and removes the whole class of accident.

Reference: docs/DECISIONS.md D-114, D-134.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path

__all__ = ["ENV_NAME", "MissingKeyError", "read_api_key"]

ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
"""What a variable *name* looks like.

Anything else in ``api_key_env`` is almost certainly the key itself, pasted
where its name belongs — which is a secret in a file that gets committed. The
settings reader refuses it by name.
"""

_FILE_SUFFIX = "_FILE"


class MissingKeyError(RuntimeError):
    """A source needs a key and the environment does not hold one.

    Raised before the fetch rather than during it: a source that answers 401 to
    everything would otherwise spend its whole request budget finding that out,
    against terms that count requests.
    """


def read_api_key(
    source_id: str, env_name: str | None, environ: Mapping[str, str] | None = None
) -> str | None:
    """The key for one source, read at the moment it is needed.

    Args:
        source_id: Whose key it is, so a refusal names it.
        env_name: The variable the settings file named, or None when the source
            needs no key.
        environ: Defaults to the process environment.

    Returns:
        The key, or None when none is needed.

    Raises:
        MissingKeyError: The named variable is unset or empty, or the file it
            points at cannot be read.

    Note:
        ``<NAME>_FILE`` wins over ``<NAME>``, the convention D-114 established
        for the platform's own secrets: a file keeps the value out of the
        process environment, where ``docker inspect`` and ``/proc/<pid>/environ``
        show it to anyone on the host. Surrounding whitespace is stripped,
        because a key written with ``>`` ends with a newline that is not part
        of it.
    """
    if env_name is None:
        return None
    env = os.environ if environ is None else environ
    from_file = env.get(env_name + _FILE_SUFFIX, "").strip()
    if from_file:
        return _from_file(Path(from_file), env_name)
    value = env.get(env_name, "").strip()
    if not value:
        message = (
            f"{source_id} needs a key from {env_name}, which is unset. Set it, or "
            f"point {env_name}{_FILE_SUFFIX} at a file holding it"
        )
        raise MissingKeyError(message)
    return value


def _from_file(path: Path, env_name: str) -> str:
    """One key read from the file a ``<NAME>_FILE`` variable points at."""
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        message = (
            f"{env_name}{_FILE_SUFFIX} names {path}, which cannot be read: "
            f"{exc.strerror}"
        )
        raise MissingKeyError(message) from exc
    if not value:
        message = f"{env_name}{_FILE_SUFFIX} names {path}, which is empty"
        raise MissingKeyError(message)
    return value
