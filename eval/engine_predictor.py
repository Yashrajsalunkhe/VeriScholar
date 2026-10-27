"""Engine-backed plagdet predictor.

Runs the real retrieval pipeline over an eval corpus:

1. Index every source document into the engine's corpus
   (`documents.extracted_text` -> `fingerprint_document` -> `chunks` +
   fingerprints).
2. For each suspicious document, run the query pipeline (`run_check`)
   against that corpus and return its merged matched regions as plagdet
   `Passage` detections in the suspicious document's char offsets.

Needs a Postgres with the engine migrations applied — `DATABASE_URL`
points at it. Use a scratch database: the harness inserts and deletes
corpus rows freely. The engine source must be importable; this module
adds `src/` to `sys.path` so the harness runs from a checkout without
installing the package.

All eval data lives under a single fixed tenant and is cleared on setup,
so repeated runs are reproducible and don't leak into each other. Rows
outside that tenant (the seeded default tenant, any platform-level
corpus with `tenant_id IS NULL`) are never touched.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from pathlib import Path
from uuid import UUID, uuid4

_ENGINE_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_ENGINE_SRC) not in sys.path:
    sys.path.insert(0, str(_ENGINE_SRC))

from sqlalchemy import text as sql_text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from noplag_engine.db import dispose_engine, get_sessionmaker  # noqa: E402
from noplag_engine.workflows.check import run_check  # noqa: E402
from noplag_engine.workflows.corpus import fingerprint_document  # noqa: E402

# Fixed tenant for all eval corpus data — keeps eval rows partitioned and
# clearable without touching anything else in the database.
EVAL_TENANT = UUID("00000000-0000-0000-0000-0000000000e7")


async def _reset_corpus(session: AsyncSession) -> None:
    # Clears only the eval tenant: the tenants delete CASCADEs to that tenant's
    # documents -> chunks. Rows belonging to other tenants or to no tenant
    # (tenant_id IS NULL) carry no eval-tenant FK, so they are never touched.
    await session.execute(
        sql_text("DELETE FROM tenants WHERE id = :t"), {"t": EVAL_TENANT}
    )
    await session.execute(
        sql_text("INSERT INTO tenants (id, name) VALUES (:t, 'eval')"),
        {"t": EVAL_TENANT},
    )
    await session.commit()


async def _index_source(session: AsyncSession, text: str) -> UUID:
    doc_id = uuid4()
    await session.execute(
        sql_text(
            "INSERT INTO documents "
            "(id, tenant_id, extracted_text, char_length, source_type) "
            "VALUES (:id, :t, :x, :l, CAST('user_upload' AS corpus_source_type))"
        ),
        {"id": doc_id, "t": EVAL_TENANT, "x": text, "l": len(text)},
    )
    await session.commit()
    await fingerprint_document(doc_id, session)
    return doc_id


async def predict_regions(session: AsyncSession, suspicious_text: str) -> list[tuple[int, int]]:
    """Return merged matched [start, end) regions in `suspicious_text`."""
    report = await run_check(suspicious_text, session, tenant_id=EVAL_TENANT)
    return report.unique_passages


async def evaluate(
    sources: Iterable[str],
    suspicious: Iterable[tuple[str, str]],
) -> dict[str, list[tuple[int, int]]]:
    """Index `sources`, then predict regions for each suspicious doc.

    `suspicious` is an iterable of `(doc_id, text)`. Returns
    `{doc_id: [(start, end), ...]}` — the engine's detected regions per
    suspicious document, ready to convert into plagdet `Passage`s.
    """
    detections: dict[str, list[tuple[int, int]]] = {}
    try:
        async with get_sessionmaker()() as session:
            await _reset_corpus(session)
            for text in sources:
                await _index_source(session, text)
            for doc_id, text in suspicious:
                detections[doc_id] = await predict_regions(session, text)
    finally:
        await dispose_engine()
    return detections
