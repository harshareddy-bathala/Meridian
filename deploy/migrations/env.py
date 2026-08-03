"""Alembic environment.

There is no SQLAlchemy metadata to autogenerate against, by design: the schema is
defined once, in ``sql/``, and an ORM model layer would be a second definition to
keep in sync by hand. Alembic is here for revision ordering and the
``alembic_version`` table, nothing else. See docs/DECISIONS.md D-019.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from meridian.config import load_settings, sqlalchemy_url

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _database_url() -> str:
    """The connection URL, in the form SQLAlchemy needs.

    Never read from alembic.ini — that file is committed and this repository is
    public.

    Delegated to ``meridian.config`` rather than rebuilt here. This file used to
    assemble its own URL from ``POSTGRES_*``, which made two places responsible
    for one connection and forced the compose file to set ``DATABASE_URL`` to a
    different value for the migration runner than for the API. ``sqlalchemy_url``
    adds the ``+psycopg`` driver suffix that libpq rejects and SQLAlchemy
    requires, so a single environment variable now serves both (D-033).

    Importing ``meridian`` here is not a new coupling: Alembic ships as a
    dependency of the ``meridian`` distribution, so anything that can run this
    file already has the package installed.
    """
    return sqlalchemy_url(load_settings().database_url)


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting."""
    context.configure(
        url=_database_url(),
        target_metadata=None,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply migrations against a live database."""
    config.set_main_option("sqlalchemy.url", _database_url())
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
