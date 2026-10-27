"""Bring-your-own-corpus ingester: index a folder of documents.

Walks a directory tree, extracts plain text from every supported file
(.txt, .md, .pdf, .docx), and indexes each as a platform-level corpus
document — the set every check matches against. Re-runs are idempotent:
a file whose path is already indexed is skipped, so you can point the
script at a growing folder and run it on a schedule.

    DATABASE_URL=postgresql+psycopg://... python scripts/ingest_folder.py /path/to/corpus

This is the simplest way to build a private corpus for a self-hosted
engine. For very large corpora you'll want to adapt it (parallelism,
batched commits); the per-document primitives it composes —
`ingest_platform_document` and `fingerprint_document` — are the same
ones any bulk pipeline would use.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from noplag_engine.db import get_sessionmaker
from noplag_engine.ingestion import (
    ExtractionFailed,
    NoExtractableText,
    UnsupportedMediaType,
    extract_text,
    normalize_text,
    resolve_mime_type,
)
from noplag_engine.workflows.corpus import (
    fingerprint_document,
    ingest_platform_document,
)

SUPPORTED_SUFFIXES = {".txt", ".md", ".pdf", ".docx"}


async def ingest_file(session, path: Path) -> str:
    """Ingest one file; returns 'indexed', 'skipped', or 'failed'."""
    data = path.read_bytes()
    if not data:
        return "skipped"
    try:
        mime_type = resolve_mime_type(None, path.name)
        text = normalize_text(extract_text(data, mime_type))
    except (UnsupportedMediaType, NoExtractableText, ExtractionFailed) as exc:
        print(f"  failed: {path} ({exc})")
        return "failed"

    doc_id = await ingest_platform_document(
        session,
        source_type="folder_import",
        source_url=str(path.resolve()),
        language="",  # detected during fingerprinting
        filename=path.name,
        extracted_text=text,
    )
    if doc_id is None:
        return "skipped"  # already indexed (same path)
    await fingerprint_document(doc_id, session)
    return "indexed"


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path, help="directory to index recursively")
    args = parser.parse_args()

    if not args.folder.is_dir():
        print(f"not a directory: {args.folder}")
        return 1

    files = sorted(
        p
        for p in args.folder.rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
    )
    if not files:
        print(f"no supported files ({', '.join(sorted(SUPPORTED_SUFFIXES))}) in {args.folder}")
        return 1

    counts = {"indexed": 0, "skipped": 0, "failed": 0}
    async with get_sessionmaker()() as session:
        for i, path in enumerate(files, 1):
            outcome = await ingest_file(session, path)
            counts[outcome] += 1
            if outcome == "indexed":
                print(f"[{i}/{len(files)}] indexed {path}")

    print(
        f"done: {counts['indexed']} indexed, {counts['skipped']} skipped "
        f"(already indexed or empty), {counts['failed']} failed"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
