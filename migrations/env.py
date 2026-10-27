"""Alembic environment.

Reads the connection string from DATABASE_URL; falls back to the
docker-compose defaults so `alembic upgrade head` works against the local
stack without extra env setup.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config

DEFAULT_DSN = "postgresql+psycopg://noplag:noplag@localhost:5432/noplag"
config.set_main_option("sqlalchemy.url", os.environ.get("DATABASE_URL", DEFAULT_DSN))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Revisions write SQL directly via op.execute / op.create_table; no model
# metadata is needed for autogeneration.
target_metadata = None


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
