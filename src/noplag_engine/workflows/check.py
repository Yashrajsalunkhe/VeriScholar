"""End-to-end check pipeline.

Wires the four core primitives — chunking, winnowing fingerprints,
L1 retrieval, L0 alignment — into a single pipeline that consumes a
query document's text and returns a structured `CheckReport`.

This module returns the report in memory; the API layer
(`api/v1/checks.py`) persists it (the `checks` table) and serves it on
`GET /v1/checks/{id}/report`.

Pipeline (per query chunk):

  1. `chunk_document(query_text)` -> Chunk[]
  2. For each chunk:
     a. `fingerprint(chunk.text)` -> int[]
     b. `retrieve_l1_candidates(fps, tenant_id=...)` -> L1Candidate[]
     c. Fetch text from `chunks_text` for each candidate id (single SELECT
        per query chunk; cached chunk_id -> document_id mapping reuses what
        L1 already returned, no extra join needed).
     d. `align(query_chunk_id, chunk.text, [(cid, text), ...])` ->
        AlignedPassage[]
     e. Translate each passage's `query_start`/`query_end` from chunk-
        local offsets to full-query offsets via `chunk.char_start`.

  3. Group translated passages by `source_document_id`; compute
     per-source matched_chars + similarity_pct; sort sources by
     similarity_pct descending.

  4. Across-source dedupe: merge all passage `(query_start, query_end)`
     intervals to produce `unique_passages` and `total_matched_chars`.
     This is what plagdet consumes.

Both `matched_chars` and `total_matched_chars` are computed over
**deduped query intervals** — the per-source figure merges overlapping
query ranges within a single source (neighbouring query chunks sharing
a sentence-overlap region count once), the cross-source figure does
the same merge across every source. Without per-source dedupe, the
Report UI ends up showing things like "Source X: 132%" when chunk
overlap double-counts shared regions; with dedupe, `similarity_pct`
reads as "fraction of the query attributable to this one source",
bounded by 100% and directly comparable across sources.

Query-chunk UUIDs
-----------------
Query chunks aren't persisted, so `AlignedPassage.query_chunk_id` is
`uuid.UUID(int=chunk_index)` — deterministic across runs, scoped per
report, and unambiguously synthetic (collision with a real persistent
UUID has cosmological-scale negligible probability).
"""

from __future__ import annotations

import asyncio
import logging
import multiprocessing
import os
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from time import monotonic
from uuid import UUID

from sqlalchemy import bindparam
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from noplag_engine.alignment import AlignedPassage, align
from noplag_engine.chunking import chunk_document
from noplag_engine.config import FingerprintConfig, get_fingerprint_config
from noplag_engine.fingerprinting import fingerprint
from noplag_engine.intervals import merge_intervals
from noplag_engine.retrieval import retrieve_l1_candidates  # noqa: F401  (kept: single-chunk API)
from noplag_engine.retrieval.l1_winnowing import (  # noqa: F401  (batched kept for callers/tests)
    batch_visit_order,
    retrieve_l1_batch,
    retrieve_l1_candidates_batched,
)
from noplag_engine.retrieval.stop_list import load_stop_list
from noplag_engine.workflows.progress import ProgressEvent

ProgressCallback = Callable[[ProgressEvent], None]

logger = logging.getLogger("noplag.workflows.check")


@dataclass(frozen=True)
class MatchedSource:
    source_document_id: UUID
    passages: list[AlignedPassage]
    matched_chars: int
    similarity_pct: float


@dataclass(frozen=True)
class CheckReport:
    query_text_length: int
    total_matched_chars: int
    overall_similarity_pct: float
    sources: list[MatchedSource]
    unique_passages: list[tuple[int, int]]
    # Coverage: "full" when every chunk was matched, "partial" when a work cap
    # (a per-check time budget) stopped retrieval early. coverage_reason names
    # the cap that fired ("time_cap"); checked/total_chunks quantify it. A
    # partial check still returns every match it found — there is no failure
    # path — so the UI surfaces it as "scan may not be exhaustive", not an error.
    coverage: str = "full"
    coverage_reason: str | None = None
    checked_chunks: int = 0
    total_chunks: int = 0


# Chunk text lives in the chunks_text side table: the chunks heap is
# kept narrow so the GIN -> heap candidate fetch touches fewer bytes, and text is
# read only for the top-K survivors keyed by chunk id.
_FETCH_CHUNK_TEXTS_SQL = sql_text(
    "SELECT chunk_id AS id, text FROM chunks_text WHERE chunk_id IN :ids"
).bindparams(bindparam("ids", expanding=True))

# Candidate-text fetches are batched at this id-count so a large deduped
# candidate set doesn't build one oversized IN array.
_FETCH_BATCH = 5000

# Query chunks per corpus scan in the retrieval loop. The loop visits these
# batches in a spread order so a time cap leaves uniform coverage.
_RETRIEVAL_BATCH = 64

# L0 alignment is pure-Python seed-extend run once per (query chunk, candidate);
# on a single-chunk paste against a top_k=200 pool it is effectively the whole
# check's wall-clock. Spread it over a reused process pool — threads can't help
# (GIL-bound). forkserver forks workers from a clean child, avoiding the
# fork-with-threads deadlock risk of forking the async/uvicorn parent directly.
_ALIGN_WORKERS = max(1, min((os.cpu_count() or 4) - 2, 16))
# Candidates per align task: small enough to spread a single chunk's pool across
# workers, large enough that per-task IPC (pickling ~300-char snippets) stays
# negligible next to the alignment cost.
_ALIGN_TASK_SIZE = 16
# Escape hatch (tests, single-core boxes): NOPLAG_ALIGN_PARALLEL=0 forces the
# inline path. The pool is also skipped automatically when there's only one task.
_ALIGN_PARALLEL = os.environ.get("NOPLAG_ALIGN_PARALLEL", "1") != "0"
_align_pool: ProcessPoolExecutor | None = None


def _get_align_pool() -> ProcessPoolExecutor:
    global _align_pool
    if _align_pool is None:
        _align_pool = ProcessPoolExecutor(
            max_workers=_ALIGN_WORKERS,
            mp_context=multiprocessing.get_context("forkserver"),
        )
    return _align_pool


def _align_task(
    query_chunk_id_int: int,
    query_text: str,
    candidates: list[tuple[UUID, str]],
) -> list[AlignedPassage]:
    """Top-level (picklable) wrapper so a pool worker can run one align batch."""
    return align(UUID(int=query_chunk_id_int), query_text, candidates)


async def run_check(
    query_text: str,
    session: AsyncSession,
    *,
    tenant_id: UUID | None = None,
    language: str = "en",
    progress_callback: ProgressCallback | None = None,
    config: FingerprintConfig | None = None,
    exclude_document_id: UUID | None = None,
    time_budget_s: float | None = None,
) -> CheckReport:
    """Run the check pipeline against `query_text` and return a `CheckReport`.

    Parameters
    ----------
    query_text : str
        Plain text of the document being checked. Offsets in the
        returned report's passages and `unique_passages` index into this
        string directly.
    session : AsyncSession
        Async SQLAlchemy session bound to the engine database.
    tenant_id : UUID | None
        When provided, L1 retrieval is scoped to chunks owned by this
        tenant (admin/internal calls pass None to query across
        tenants).
    language : str
        ISO 639-1 language code passed to `chunk_document` for sentence
        segmentation (default "en").
    progress_callback : Callable[[ProgressEvent], None] | None
        Optional sync callback fired at stage boundaries. Used by the
        API layer to drive the SSE progress stream. The
        callback is invoked synchronously inside the pipeline; keep it
        cheap (e.g. enqueue and return).

    Returns
    -------
    CheckReport
        Sources sorted by `similarity_pct` descending. Empty `sources`
        and zero `total_matched_chars` when the query is empty or no
        candidates align.
    """
    emit = progress_callback if progress_callback is not None else _noop_progress

    if not query_text:
        emit(ProgressEvent(stage="complete", chunks_done=0, chunks_total=0))
        return _empty_report(0)

    cfg = config if config is not None else get_fingerprint_config()

    # Per-phase timing — logged once at the end so we can see where a check's
    # wall-clock actually goes (chunk / fingerprint / retrieve / fetch / align /
    # assemble) instead of guessing. Cheap (a few monotonic() reads).
    _t = monotonic()
    timing = {"chunk": 0.0, "fp": 0.0, "retrieve": 0.0, "fetch": 0.0, "align": 0.0, "assemble": 0.0}

    emit(ProgressEvent(stage="chunking", chunks_done=0, chunks_total=0))
    chunks = chunk_document(
        query_text,
        sentences_per_chunk=cfg.sentences_per_chunk,
        overlap=cfg.chunk_overlap,
        language=language,
    )
    if not chunks:
        emit(ProgressEvent(stage="complete", chunks_done=0, chunks_total=0))
        return _empty_report(len(query_text))
    chunks_total = len(chunks)
    timing["chunk"] = monotonic() - _t

    # Phase 1 — fingerprint every chunk up front. Deliberately uncapped: it's
    # cheap (~1s even for thousands of chunks) and capping here would bias
    # detection toward the document's opening chunks. Stop fingerprints (high-DF,
    # non-discriminative) are dropped here so the per-batch scans stay cheap.
    emit(ProgressEvent(stage="fingerprinting", chunks_done=0, chunks_total=chunks_total))
    _t = monotonic()
    chunk_fps = [fingerprint(chunk.text, k=cfg.k, w=cfg.w) for chunk in chunks]
    stop_list = await load_stop_list(session)
    if stop_list.fingerprints:
        stop_fps = stop_list.fingerprints
        chunk_fps = [[f for f in fps if f not in stop_fps] for fps in chunk_fps]
    timing["fp"] = monotonic() - _t

    # Phases 2+3 — retrieve candidates and align, one corpus scan per batch of
    # chunks (the lever that keeps a many-chunk document off the per-query-I/O
    # wall). Batches are visited in a spread order, and an optional per-check
    # time budget caps the I/O-heavy retrieval/text-fetch work: if it fires the
    # loop stops early and the check returns partial — but uniform — coverage
    # plus every match found so far. The cap never raises.
    emit(ProgressEvent(stage="retrieving", chunks_done=0, chunks_total=chunks_total))
    batch_starts = list(range(0, chunks_total, _RETRIEVAL_BATCH))
    deadline = monotonic() + time_budget_s if time_budget_s else None

    # (source_document_id, translated AlignedPassage in full-query coords)
    flat: list[tuple[UUID, AlignedPassage]] = []
    checked_chunks = 0
    coverage_reason: str | None = None
    for bi in batch_visit_order(len(batch_starts)):
        if deadline is not None and monotonic() > deadline:
            coverage_reason = "time_cap"
            break
        start = batch_starts[bi]
        group = list(range(start, min(start + _RETRIEVAL_BATCH, chunks_total)))
        _tr = monotonic()
        batch = await retrieve_l1_batch(
            group,
            chunk_fps,
            session,
            tenant_id=tenant_id,
            exclude_document_id=exclude_document_id,
        )
        timing["retrieve"] += monotonic() - _tr
        checked_chunks += len(group)
        if batch:
            cand_ids = {c.chunk_id for cands in batch.values() for c in cands}
            _tf = monotonic()
            texts = await _fetch_chunk_texts(session, list(cand_ids))
            timing["fetch"] += monotonic() - _tf
            doc_of = {
                c.chunk_id: c.document_id for cands in batch.values() for c in cands
            }
            emit(
                ProgressEvent(
                    stage="aligning", chunks_done=checked_chunks, chunks_total=chunks_total
                )
            )
            # Align across the box's cores: split each query chunk's candidate pool
            # into worker-sized jobs. This collapses the dominant single-chunk cost
            # (top_k align, previously serial) to ~1/Nworkers of that. Falls back to
            # inline when parallelism is disabled or there's a single job (tests,
            # tiny checks) — avoids paying pool spin-up for trivial work.
            _ta = monotonic()
            align_jobs: list[tuple[int, list[tuple[UUID, str]]]] = []
            for i, cands in batch.items():
                candidates = [
                    (c.chunk_id, texts[c.chunk_id]) for c in cands if c.chunk_id in texts
                ]
                if not candidates:
                    continue
                for s in range(0, len(candidates), _ALIGN_TASK_SIZE):
                    align_jobs.append((i, candidates[s : s + _ALIGN_TASK_SIZE]))

            if _ALIGN_PARALLEL and len(align_jobs) > 1:
                loop = asyncio.get_running_loop()
                pool = _get_align_pool()
                results = await asyncio.gather(
                    *[
                        loop.run_in_executor(pool, _align_task, i, chunks[i].text, sub)
                        for i, sub in align_jobs
                    ]
                )
            else:
                results = [_align_task(i, chunks[i].text, sub) for i, sub in align_jobs]

            for (i, _sub), passages in zip(align_jobs, results, strict=True):
                for passage in passages:
                    doc_id = doc_of[passage.candidate_chunk_id]
                    flat.append(
                        (doc_id, _translate_to_full_query(passage, chunks[i].char_start))
                    )
            timing["align"] += monotonic() - _ta

    coverage = "partial" if coverage_reason else "full"
    if coverage == "full":
        checked_chunks = chunks_total

    emit(
        ProgressEvent(
            stage="assembling", chunks_done=chunks_total, chunks_total=chunks_total
        )
    )
    _t = monotonic()
    report = _build_report(
        query_text,
        flat,
        coverage=coverage,
        coverage_reason=coverage_reason,
        checked_chunks=checked_chunks,
        total_chunks=chunks_total,
    )
    timing["assemble"] = monotonic() - _t
    # Per-phase timing at DEBUG: invaluable for spotting which stage owns a
    # check's wall-clock, silent at the default INFO. Enable with the
    # noplag.workflows.check logger.
    logger.debug(
        "CHECK_TIMING chunks=%d candidates=%d | chunk=%.2f fp=%.2f retrieve=%.2f "
        "fetch=%.2f align=%.2f assemble=%.2f total=%.2f",
        chunks_total,
        len(flat),
        timing["chunk"],
        timing["fp"],
        timing["retrieve"],
        timing["fetch"],
        timing["align"],
        timing["assemble"],
        sum(timing.values()),
    )
    emit(
        ProgressEvent(
            stage="complete", chunks_done=chunks_total, chunks_total=chunks_total
        )
    )
    return report


def _noop_progress(_event: ProgressEvent) -> None:
    pass


async def _fetch_chunk_texts(
    session: AsyncSession,
    chunk_ids: list[UUID],
) -> dict[UUID, str]:
    """Return `{chunk_id: text}` for the given IDs. Used by the workflow
    to pair L1 candidate IDs with the text the L0 aligner needs.

    Internal to the check pipeline — L1's public surface returns IDs +
    fingerprint counts only (the candidate pool is sometimes large; the
    workflow pays for the text fetch only once per query chunk, after
    Python-side scoring has trimmed to the top-K).
    """
    if not chunk_ids:
        return {}
    # A whole-document check can accumulate a large deduped candidate set; fetch
    # in fixed batches so the id array stays a sane size per query.
    out: dict[UUID, str] = {}
    for start in range(0, len(chunk_ids), _FETCH_BATCH):
        batch = chunk_ids[start : start + _FETCH_BATCH]
        result = await session.execute(_FETCH_CHUNK_TEXTS_SQL, {"ids": batch})
        out.update({row.id: row.text for row in result.all()})
    return out


def _translate_to_full_query(
    passage: AlignedPassage,
    chunk_char_start: int,
) -> AlignedPassage:
    """Shift a chunk-local passage's query offsets into full-query coords."""
    return AlignedPassage(
        query_chunk_id=passage.query_chunk_id,
        candidate_chunk_id=passage.candidate_chunk_id,
        query_start=chunk_char_start + passage.query_start,
        query_end=chunk_char_start + passage.query_end,
        candidate_start=passage.candidate_start,
        candidate_end=passage.candidate_end,
        score=passage.score,
    )


def _build_report(
    query_text: str,
    flat: list[tuple[UUID, AlignedPassage]],
    *,
    coverage: str = "full",
    coverage_reason: str | None = None,
    checked_chunks: int = 0,
    total_chunks: int = 0,
) -> CheckReport:
    query_length = len(query_text)
    cov = {
        "coverage": coverage,
        "coverage_reason": coverage_reason,
        "checked_chunks": checked_chunks,
        "total_chunks": total_chunks,
    }
    if not flat:
        return _empty_report(query_length, **cov)

    def _source(document_id: UUID, passages: list[AlignedPassage]) -> MatchedSource:
        passages_sorted = sorted(
            passages, key=lambda p: (p.query_start, p.candidate_chunk_id)
        )
        # Dedupe overlapping query coverage within the source before summing —
        # neighbouring query chunks overlap by one sentence, so raw
        # summing would inflate similarity_pct beyond 100% on any source that
        # overlaps more than one query chunk. Same interval merge as cross-source.
        within = merge_intervals([(p.query_start, p.query_end) for p in passages_sorted])
        matched_chars = sum(end - start for start, end in within)
        return MatchedSource(
            source_document_id=document_id,
            passages=passages_sorted,
            matched_chars=matched_chars,
            similarity_pct=matched_chars / query_length * 100 if query_length else 0.0,
        )

    by_doc: dict[UUID, list[AlignedPassage]] = defaultdict(list)
    for document_id, passage in flat:
        by_doc[document_id].append(passage)

    sources = [_source(document_id, p) for document_id, p in by_doc.items()]
    sources.sort(key=lambda s: (-s.similarity_pct, str(s.source_document_id)))

    unique_passages = merge_intervals(
        [(p.query_start, p.query_end) for _, p in flat]
    )
    total_matched_chars = sum(end - start for start, end in unique_passages)
    overall_similarity_pct = (
        total_matched_chars / query_length * 100 if query_length else 0.0
    )

    return CheckReport(
        query_text_length=query_length,
        total_matched_chars=total_matched_chars,
        overall_similarity_pct=overall_similarity_pct,
        sources=sources,
        unique_passages=unique_passages,
        **cov,
    )


def _empty_report(
    query_length: int,
    *,
    coverage: str = "full",
    coverage_reason: str | None = None,
    checked_chunks: int = 0,
    total_chunks: int = 0,
) -> CheckReport:
    return CheckReport(
        query_text_length=query_length,
        total_matched_chars=0,
        overall_similarity_pct=0.0,
        sources=[],
        unique_passages=[],
        coverage=coverage,
        coverage_reason=coverage_reason,
        checked_chunks=checked_chunks,
        total_chunks=total_chunks,
    )
