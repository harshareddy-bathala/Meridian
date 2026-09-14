"""Which migration the database is at, and which the running code expects.

The API refuses nothing when the two differ — compose already refuses to start it
until ``migrate`` has succeeded — but a database restored from an older backup,
or a container started by hand, can still leave the code ahead of the schema.
That is reported as a metric and by ``meridian db status`` rather than discovered
as a missing column in the middle of a heartbeat.

The expected revision is read from the migration scripts through
``deploy/alembic.ini``, the same file the ``migrate`` service runs, so the two can
never disagree about where the scripts are.

Reference: docs/DECISIONS.md D-019, D-109, D-111.
"""

from __future__ import annotations

from pathlib import Path

import psycopg
from alembic.config import Config
from alembic.script import ScriptDirectory

from meridian.store.stations import Connection

__all__ = ["ALEMBIC_CONFIG_PATH", "find_current_revision", "find_head_revision"]

ALEMBIC_CONFIG_PATH = Path("deploy/alembic.ini")
"""Relative to the working directory, as the ``migrate`` service invokes it.

The image's working directory is ``/app`` and a checkout's is its root; both have
``deploy/alembic.ini`` beneath them.
"""


def find_head_revision(config_path: Path = ALEMBIC_CONFIG_PATH) -> str | None:
    """The newest migration revision the scripts define.

    Args:
        config_path: The alembic configuration naming the script directory.

    Returns:
        The head revision, or ``None`` when the configuration is not there — a
        process started outside the image and outside a checkout has no way to
        know, and reporting a guess would be worse than reporting nothing.

    Note:
        Loading the configuration applies its ``prepend_sys_path``, which is how
        the revision files find their shared helper. That changes ``sys.path``
        for the process, once.
    """
    if not config_path.is_file():
        return None
    return ScriptDirectory.from_config(Config(str(config_path))).get_current_head()


def find_current_revision(conn: Connection) -> str | None:
    """The revision recorded in ``alembic_version``, or ``None`` before any.

    Args:
        conn: An open connection. A missing table is read inside a savepoint,
            so the caller's transaction survives it.
    """
    try:
        with conn.transaction(), conn.cursor() as cur:
            cur.execute("select version_num from alembic_version")
            row = cur.fetchone()
    except psycopg.errors.UndefinedTable:
        return None
    return None if row is None else str(row[0])
