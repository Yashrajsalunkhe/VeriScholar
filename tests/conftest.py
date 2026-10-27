"""Shared fixtures for the engine test suite.

Integration tests that need real Postgres (L1 retrieval, the check
workflow, the API surface) consume the `session` fixture. API tests
consume `async_client` on top of that. Tests that don't need a DB
(chunking, fingerprinting, alignment) ignore both.

`NOPLAG_TEST_DATABASE_URL` env var overrides the docker-compose default.
The resolved URL is pushed into `DATABASE_URL` so the engine's
sessionmaker (`db`) and the API's background task hit the same DB the
test session migrated.

When the DB is unreachable, all DB-dependent tests are skipped — that's
how the chunking / fingerprinting / alignment suites stay green in any
CI lane without Postgres available.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy import text as sql_text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

DEFAULT_URL = "postgresql+psycopg://noplag:noplag@localhost:5432/noplag"
TEST_URL = os.environ.get(
    "NOPLAG_TEST_DATABASE_URL", os.environ.get("DATABASE_URL", DEFAULT_URL)
)
# Push the resolved URL into DATABASE_URL so the engine's sessionmaker
# (lazy-init in noplag_engine.db) reads the same DB the tests migrated.
os.environ["DATABASE_URL"] = TEST_URL


def _probe(url: str) -> str | None:
    try:
        eng = create_engine(url)
        with eng.connect() as conn:
            conn.execute(sql_text("SELECT 1"))
        eng.dispose()
    except OperationalError as exc:
        return str(exc)
    return None


@pytest.fixture(scope="session")
def _migrated_db() -> str:
    reason = _probe(TEST_URL)
    if reason:
        pytest.skip(f"Postgres not reachable at {TEST_URL}: {reason}")
    engine_dir = Path(__file__).resolve().parent.parent
    cfg = Config(str(engine_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(engine_dir / "migrations"))
    cfg.set_main_option("sqlalchemy.url", TEST_URL)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    return TEST_URL


@pytest_asyncio.fixture
async def session(_migrated_db: str) -> AsyncIterator[AsyncSession]:
    """Per-test AsyncSession with a freshly-truncated corpus.

    `tenants CASCADE` chains through documents → chunks and through
    checks → check_results, so this clears engine state for the test.
    The default tenant is re-seeded because the API depends on it.
    """
    engine = create_async_engine(_migrated_db, poolclass=NullPool)
    async with engine.connect() as conn:
        await conn.execute(sql_text("TRUNCATE tenants CASCADE"))
        await conn.execute(sql_text("TRUNCATE documents CASCADE"))
        await conn.execute(
            sql_text(
                "INSERT INTO tenants (id, name) "
                "VALUES ('00000000-0000-4000-8000-000000000001', 'default')"
            )
        )
        await conn.commit()
    sess = AsyncSession(engine, expire_on_commit=False)
    try:
        yield sess
    finally:
        await sess.close()
        await engine.dispose()


@pytest_asyncio.fixture
async def async_client(session: AsyncSession):
    """FastAPI app + httpx AsyncClient. Endpoint DB access is routed to
    the per-test `session` via dependency override; the background task
    still opens its own session from the sessionmaker (which points at
    the same DATABASE_URL = TEST_URL by way of the module-level setenv
    above)."""
    from httpx import ASGITransport, AsyncClient

    from noplag_engine.api.app import create_app
    from noplag_engine.api.deps import get_session
    from noplag_engine.db import dispose_engine
    from noplag_engine.workflows.progress import reset_for_tests

    reset_for_tests()
    await dispose_engine()

    async def _override_get_session() -> AsyncIterator[AsyncSession]:
        yield session

    app = create_app()
    app.dependency_overrides[get_session] = _override_get_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    reset_for_tests()
    await dispose_engine()
