"""Explicit ANALYZE for corpus tables after a bulk-load campaign.

The L1 ``fingerprints && :q`` retrieval depends on up-to-date planner
statistics; stale stats can cost orders of magnitude in latency (the planner
mis-estimates ``&&`` selectivity and seq-scans the whole corpus). Neither of
the two situations that produce stale stats is self-healing:

- A ``pg_basebackup`` restore zeroes the cumulative statistics views, so on a
  quiescent already-loaded table ``n_mod_since_analyze`` is 0 and autoanalyze
  never fires.
- A finished bulk load leaves the table quiescent the moment writes stop.

So ANALYZE explicitly at the end of every corpus ingestion campaign and after
every restore. Autovacuum keeps stats fresh *during* long loads but cannot
cover the quiescent cases above.

Two entry points: ``analyze_corpus_tables`` for an ingestion pipeline to await
at campaign end, and a standalone CLI — ``python -m noplag_engine.maintenance.analyze``.
"""

from __future__ import annotations

import asyncio
import logging
import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

logger = logging.getLogger("noplag.maintenance.analyze")

# Tables whose planner statistics drive retrieval. ANALYZE is cheap relative to a
# bulk load (~2s for chunks at statistics target 500) and safe to over-call, so
# this is a small fixed allowlist rather than anything dynamic. Not user input —
# no SQL-injection surface in the f-strings below.
CORPUS_TABLES: tuple[str, ...] = ("chunks", "documents")


async def analyze_corpus_tables(
    session: AsyncSession, tables: tuple[str, ...] = CORPUS_TABLES
) -> None:
    """ANALYZE the corpus tables on an existing async session, then commit.

    ANALYZE is permitted inside a transaction (unlike VACUUM); the refreshed
    statistics become visible on commit. Call once at the end of an ingestion
    campaign — not per shard/batch (autovacuum covers in-flight freshness).
    """
    for table in tables:
        logger.info("ANALYZE %s", table)
        await session.execute(text(f"ANALYZE {table}"))
    await session.commit()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    dsn = os.environ.get(
        "DATABASE_URL", "postgresql+psycopg://noplag:noplag@localhost:5432/noplag"
    )

    async def _run() -> None:
        eng = create_async_engine(dsn)
        try:
            # eng.begin() wraps the ANALYZEs in one short transaction and commits
            # on exit; fine for ANALYZE (it is transaction-safe).
            async with eng.begin() as conn:
                for table in CORPUS_TABLES:
                    logger.info("ANALYZE %s ...", table)
                    await conn.execute(text(f"ANALYZE {table}"))
            logger.info("done")
        finally:
            await eng.dispose()

    asyncio.run(_run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
