"""Least-privilege database roles: the grants, the logins and their passwords (D-207).

Three kinds of connection reach the database, and each gets only what it needs:

- **the owner** — the bootstrap user the database image creates. It runs the
  migrations and this module, owns every table, and is the only role that can
  change the schema. It is a superuser, because TimescaleDB's extension needs one
  to be created or updated.
- **``meridian_api``** — the API, the jobs process and the operator CLI inside
  their containers. A member of ``meridian_readwrite``: select, insert, update and
  delete on every table, and nothing else. It cannot create, alter, drop or
  truncate anything, and cannot create a temporary table.
- **``meridian_reader``** — ad-hoc queries and anything that only reads, such as a
  snapshot export. A member of ``meridian_readonly``: select only.

The two group roles hold the privileges and the two login roles hold passwords,
so a login can be replaced without regranting anything.

Everything here is idempotent, and it runs after every migration: a restore that
dropped the grants (``pg_restore --no-acl``), a migration adding a table, and a
changed password are all brought into line by running it again. Default
privileges cover tables the owner creates later, and running this again covers
anything created some other way.

Roles belong to the whole cluster, not one database, so creating one that
already exists is not an error here: another database on the same server, such
as a test's, may have created it.

Reference: docs/DECISIONS.md D-207.
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg
from psycopg import sql

__all__ = [
    "API_LOGIN",
    "READER_LOGIN",
    "READONLY_ROLE",
    "READWRITE_ROLE",
    "LoginPasswords",
    "ensure_database_roles",
]

READWRITE_ROLE = "meridian_readwrite"
READONLY_ROLE = "meridian_readonly"
API_LOGIN = "meridian_api"
READER_LOGIN = "meridian_reader"

_SCHEMA = "public"

Connection = psycopg.Connection[tuple[object, ...]]


@dataclass(frozen=True, slots=True)
class LoginPasswords:
    """The passwords the two login roles are given, in plain text."""

    api: str
    reader: str


def ensure_database_roles(conn: Connection, passwords: LoginPasswords) -> None:
    """Create or update the roles, grants and passwords, in one transaction.

    Args:
        conn: A connection as the owner, to the database the platform uses.
        passwords: What each login role's password is to be.
    """
    database = conn.info.dbname
    with conn.transaction(), conn.cursor() as cur:
        for role in (READWRITE_ROLE, READONLY_ROLE):
            _ensure_role(cur, role)
        for login, group, password in (
            (API_LOGIN, READWRITE_ROLE, passwords.api),
            (READER_LOGIN, READONLY_ROLE, passwords.reader),
        ):
            _ensure_role(cur, login)
            _set_login(conn, cur, login, group, password)
        for statement in _grants(database):
            cur.execute(statement)


def _ensure_role(cur: psycopg.Cursor[tuple[object, ...]], role: str) -> None:
    """Create ``role`` as a plain, non-login role if the cluster has none."""
    cur.execute("select 1 from pg_roles where rolname = %s", (role,))
    if cur.fetchone() is None:
        cur.execute(sql.SQL("create role {} nologin").format(sql.Identifier(role)))


def _set_login(
    conn: Connection,
    cur: psycopg.Cursor[tuple[object, ...]],
    login: str,
    group: str,
    password: str,
) -> None:
    """Make ``login`` a member of ``group`` that can log in with ``password``.

    The password is hashed here, by libpq, into the SCRAM verifier the server
    stores, so the plain text never crosses the connection or reaches a server
    log that records statements.
    """
    verifier = conn.pgconn.encrypt_password(
        password.encode("utf-8"), login.encode("utf-8"), b"scram-sha-256"
    )
    cur.execute(
        sql.SQL(
            "alter role {} with login nosuperuser nocreatedb nocreaterole"
            " noreplication nobypassrls inherit password {}"
        ).format(sql.Identifier(login), sql.Literal(verifier.decode("ascii")))
    )
    cur.execute(
        sql.SQL("grant {} to {}").format(sql.Identifier(group), sql.Identifier(login))
    )


def _grants(database: str) -> list[sql.Composed]:
    """Every privilege statement, in the order they must run."""
    readwrite = sql.Identifier(READWRITE_ROLE)
    readonly = sql.Identifier(READONLY_ROLE)
    schema = sql.Identifier(_SCHEMA)
    db = sql.Identifier(database)
    both = sql.SQL(", ").join([readwrite, readonly])
    statements = [
        # Nobody but the owner creates anything: not in the schema, and not a
        # temporary table in the session. PostgreSQL 15 already withholds the
        # first from PUBLIC; saying so keeps it true on an older server.
        sql.SQL("revoke create on schema {} from public").format(schema),
        sql.SQL("revoke temporary on database {} from public").format(db),
        sql.SQL("grant connect on database {} to {}").format(db, both),
        sql.SQL("grant usage on schema {} to {}").format(schema, both),
        sql.SQL(
            "grant select, insert, update, delete on all tables in schema {} to {}"
        ).format(schema, readwrite),
        sql.SQL("grant usage, select on all sequences in schema {} to {}").format(
            schema, readwrite
        ),
        sql.SQL("grant select on all tables in schema {} to {}").format(
            schema, readonly
        ),
        sql.SQL("grant select on all sequences in schema {} to {}").format(
            schema, readonly
        ),
    ]
    # For tables the owner creates in later migrations. `for role current_user`
    # is implied: default privileges are the creating role's own.
    statements += [
        sql.SQL(
            "alter default privileges in schema {} grant select, insert, update,"
            " delete on tables to {}"
        ).format(schema, readwrite),
        sql.SQL(
            "alter default privileges in schema {} grant usage, select on sequences"
            " to {}"
        ).format(schema, readwrite),
        sql.SQL(
            "alter default privileges in schema {} grant select on tables to {}"
        ).format(schema, readonly),
    ]
    return statements
