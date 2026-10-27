"""Async SQLAlchemy engine + sessionmaker singletons.

The API surface (`api/v1/checks.py`) and the background tasks need a
session factory they can construct sessions from on demand —
request-scoped sessions for endpoints, fresh per-call sessions for
background work. Both pull from the same singletons defined here.

Initialization is lazy: the engine isn't created until the first
`get_engine()` call. That lets the test conftest set `DATABASE_URL` to
the test instance before any production code touches the module.
"""

from __future__ import annotations

import os

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

_DEFAULT_DSN = "postgresql+psycopg://noplag:noplag@localhost:5432/noplag"

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _database_url() -> str:
    return os.environ.get("DATABASE_URL", _DEFAULT_DSN)


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(_database_url(), pool_pre_ping=True)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _sessionmaker


async def dispose_engine() -> None:
    """Drop the cached engine + sessionmaker. Used by tests that need to
    re-point the engine at a different DATABASE_URL between sessions."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
