"""Load the bundled Wikipedia sample corpus into the database.

Reads `data/sample_corpus.jsonl.gz` (built by
`scripts/build_sample_corpus.py`) and indexes each article as a
platform-level corpus document, so a fresh install has something real to
match against. Idempotent: articles whose URL is already indexed are
skipped, so re-running (every container start does) is cheap.

    DATABASE_URL=postgresql+psycopg://... python scripts/load_sample_corpus.py

Options:
    --limit N     load only the first N articles
    --if-empty    exit immediately when any sample document exists
                  (the fast path for container startup)
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy import text as sql_text

from noplag_engine.db import get_sessionmaker
from noplag_engine.workflows.corpus import (
    fingerprint_document,
    ingest_platform_document,
)

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "sample_corpus.jsonl.gz"


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--if-empty", action="store_true")
    args = parser.parse_args()

    if not DATA_PATH.exists():
        print(f"sample corpus artifact missing: {DATA_PATH}")
        return 1

    with gzip.open(DATA_PATH, "rt", encoding="utf-8") as fh:
        records = [json.loads(line) for line in fh if line.strip()]
    if args.limit:
        records = records[: args.limit]

    started = time.monotonic()
    loaded = skipped = 0
    async with get_sessionmaker()() as session:
        if args.if_empty:
            existing = (
                await session.execute(
                    sql_text(
                        "SELECT count(*) FROM documents WHERE source_type = 'wikipedia'"
                    )
                )
            ).scalar_one()
            if existing:
                print(f"sample corpus already loaded ({existing} documents); nothing to do")
                return 0

        for i, record in enumerate(records, 1):
            doc_id = await ingest_platform_document(
                session,
                source_type="wikipedia",
                source_url=record["url"],
                language="en",
                filename=record["title"],
                extracted_text=record["text"],
            )
            if doc_id is None:
                skipped += 1
                continue
            await fingerprint_document(doc_id, session, language="en")
            loaded += 1
            if i % 100 == 0:
                print(f"  {i}/{len(records)} ({time.monotonic() - started:.0f}s)")

    print(
        f"sample corpus: {loaded} loaded, {skipped} already present "
        f"({time.monotonic() - started:.0f}s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
