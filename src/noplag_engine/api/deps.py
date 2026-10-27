"""FastAPI dependencies.

The engine runs single-tenant: every request operates as the default
tenant seeded by the initial migration. There is no authentication layer
— run the engine on a trusted network (or behind your own reverse-proxy
auth) and treat the API as an internal service. `NOPLAG_TENANT_ID`
overrides the tenant UUID for deployments that layer their own
multi-tenancy on top.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from noplag_engine.db import get_sessionmaker

# Matches the tenant row seeded in migration 0001.
DEFAULT_TENANT_ID = UUID("00000000-0000-4000-8000-000000000001")


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield a fresh AsyncSession per request.

    Tests override this with `app.dependency_overrides[get_session]` so
    request-scoped DB work hits the same connection the test asserts on.
    """
    async with get_sessionmaker()() as session:
        yield session


async def get_tenant_id() -> UUID:
    """The tenant every request runs as (single-tenant deployment)."""
    configured = os.environ.get("NOPLAG_TENANT_ID")
    return UUID(configured) if configured else DEFAULT_TENANT_ID
