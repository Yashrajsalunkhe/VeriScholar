"""L1 retrieval — winnowing-fingerprint overlap candidates.

Given a query chunk's fingerprints, return the top-K corpus chunks ranked
by intersection cardinality against the GIN-indexed bigint[] fingerprint
column on `chunks`. This is the first stage of the four-layer retrieval
cascade.

Two-step retrieve-then-score
----------------------------
Scoring is split between Postgres and Python:

1. **Indexed candidate retrieval (Postgres).** A single
   `fingerprints && :query_fps` predicate hits the GIN index and returns
   every chunk that shares at least one fingerprint with the query.
   Optional tenant scoping is applied in the same WHERE clause. The
   candidate pool is capped at `min(top_k * 10, 2000)` so a pathological
   query that broad-matches against the entire corpus still returns in
   bounded time.

2. **Cardinality scoring (Python).** For each candidate row, compute the
   exact set-intersection size against the query fingerprints and sort
   descending. Ties break on `chunk_id` ascending so the order is
   deterministic for tests and downstream caching.

Alternatives considered and rejected:

- *Inline cardinality scoring in SQL*
  (`(SELECT count(*) FROM unnest(fingerprints) f WHERE f = ANY(:query_fps))`)
  works, but the Postgres planner sometimes mis-estimates costs on the
  unnest CTE and the count expression runs against every survivor
  regardless of selectivity.
- *intarray's cardinality operators via the `gin__int_ops` opclass.*
  Unavailable here: `gin__int_ops` ships with the intarray extension and
  applies only to int4[]. Fingerprints are bigint[] (64-bit hashes give
  effectively zero collision risk at the projected ~10⁹-chunk scale),
  so the schema uses the built-in
  `array_ops` GIN opclass which supports `&&` but not the intarray
  cardinality operators.

Python-side scoring stays well under the network-round-trip budget for
the candidate pool sizes we actually use (~2000 rows by O(tens) of
fingerprints each). If profiling later shows it's a bottleneck, push the
scoring into a stored function — the public API does not change.

Tenant scoping
--------------
When `tenant_id` is provided, results are restricted to chunks whose
denormalized `chunks.tenant_id` matches, plus the platform-level corpus
(tenant_id IS NULL). The denormalization keeps the predicate index-
friendly without a join. When `tenant_id` is None, no tenant filter is
applied (internal / cross-tenant use).
"""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from dataclasses import dataclass
from time import monotonic as _now
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.types import BigInteger

from noplag_engine.retrieval.fingerprint_df import fetch_document_frequencies

logger = logging.getLogger("noplag.retrieval.l1")

# Rarest-fingerprint retrieval. The `&&` probes only each query chunk's rarest
# fingerprints (lowest document frequency, per the fingerprint_df table) so the
# candidate pool stays small and isn't truncated before the true source is
# scored. Selection is by a cumulative-DF *budget*, not a fixed K: walk the
# chunk's fingerprints rarest-first, adding them while the running sum of their
# document frequencies stays under the budget. A fixed K fails on a chunk of all
# common phrases (no rare fingerprint) — its "rarest" K can still include a
# DF-tens-of-thousands fingerprint that alone overflows the pool; the budget
# drops that one and keeps only the genuinely-discriminative few. A chunk rich in
# rare fingerprints (each adding ~nothing to the sum) keeps many, for coverage.
# The pool the budget implies stays under _BATCH_POOL_CAP, so the `&&` LIMIT
# never truncates a real match. Override with NOPLAG_L1_DF_BUDGET. When the
# fingerprint_df table is absent (CI, eval scratch, pre-build) retrieval falls
# back to the full union.
_DF_BUDGET = int(os.environ.get("NOPLAG_L1_DF_BUDGET", "15000"))
# Hard cap on probe fingerprints per chunk so a chunk made entirely of rare
# fingerprints doesn't build a giant `&&` array (each is cheap, but thousands of
# them is wasteful). Reached only by all-rare chunks; the budget binds first
# otherwise.
_MAX_PROBE_FPS = int(os.environ.get("NOPLAG_L1_MAX_PROBE_FPS", "96"))

# Batched retrieval (retrieve_l1_candidates_batched): how many query chunks share
# one corpus scan, and the per-scan row cap. A whole-document check fingerprints
# hundreds to thousands of chunks; issuing one `&&` query per chunk against a
# 100M+-row corpus is dominated by per-query I/O, so a large document becomes
# thousands of scans -> minutes. One scan over the union of a batch's rarest
# fingerprints amortizes that I/O across the batch. The pool cap is the `&&` row
# LIMIT; the per-chunk DF budget keeps the real pool well under it, so it only
# guards against a degenerate batch.
_BATCH_SIZE = 64
# The `&&` row LIMIT. This is what actually bounds rows fetched+deserialized into
# Python for scoring (the DF budget does not: fingerprints below the DF-table
# floor read as DF 0 yet still match up to floor-1 rows each, so the row count
# pins to this cap). Profiling showed fetching 20k rows' bigint[] arrays = ~20s/
# check, so it's tunable. Override with NOPLAG_L1_POOL_CAP.
_BATCH_POOL_CAP = int(os.environ.get("NOPLAG_L1_POOL_CAP", "20000"))

# Non-discriminative-chunk skip. A chunk whose *rarest* surviving fingerprint
# still has a document frequency at or above this threshold is made entirely of
# very common phrasing — every fingerprint already matches tens of thousands of
# corpus docs. Retrieving it probes a giant posting list (the latency wall the
# pool cap can't help: a single common fingerprint's `&&` scan) only to return a
# pool too noisy to surface any one true source. Skipping is recall-neutral in
# practice: a real match shares the chunk's *rare* fingerprints, and a chunk with
# none can't be discriminated from the corpus even if retrieved (its true source,
# if any, sits below the pool cap among tens of thousands of common-phrase docs).
# The threshold is data-driven: across a 30-chunk recall benchmark on a
# web-scale corpus the rarest fingerprint of a true-positive chunk topped out
# at DF ~17k, while the
# non-discriminative band that drives the worst-case scans is DF 1e5-1e6 (max
# non-stopped DF ~1.1M). 50k leaves ~3x margin over the real-match ceiling and
# sits below the whole wall band. Only applies when fingerprint_df is present;
# set NOPLAG_L1_NONDISCRIM_DF=0 to disable the skip.
_NONDISCRIM_DF = int(os.environ.get("NOPLAG_L1_NONDISCRIM_DF", "50000"))


def _is_discriminative(fingerprints: list[int], df: dict[int, int]) -> bool:
    """True when the chunk has at least one fingerprint rarer than the
    non-discriminative threshold. Fingerprints below the DF-table floor read as
    DF 0, so any chunk carrying a genuinely rare fingerprint is always kept."""
    if _NONDISCRIM_DF <= 0:
        return True
    return any(df.get(f, 0) < _NONDISCRIM_DF for f in fingerprints)


def _select_probe_fps(
    fingerprints: list[int],
    df: dict[int, int],
    *,
    budget: int = _DF_BUDGET,
    max_fps: int = _MAX_PROBE_FPS,
) -> list[int]:
    """The rarest fingerprints whose document frequencies sum within `budget`.

    Walks `fingerprints` rarest-first (those absent from `df` are below the build
    floor, so they sort first as DF 0; ties break on value for determinism) and
    accumulates them while the running DF sum stays within `budget` and the count
    within `max_fps`. The rarest fingerprint is always included even if its own DF
    exceeds the budget, so a chunk never produces an empty probe. The combined
    posting lists of the result stay roughly within `budget`, bounding the `&&`
    pool.
    """
    ranked = sorted(fingerprints, key=lambda f: (df.get(f, 0), f))
    out: list[int] = []
    total = 0
    for f in ranked:
        d = df.get(f, 0)
        if out and (total + d > budget or len(out) >= max_fps):
            break
        out.append(f)
        total += d
    return out


@dataclass(frozen=True)
class L1Candidate:
    chunk_id: UUID
    document_id: UUID
    score: int
    fingerprint_count: int


def _candidates_sql(*, tenant_id: UUID | None, exclude_document_id: UUID | None, limit: int) -> str:
    sql = (
        "SELECT id, document_id, fingerprints FROM chunks WHERE fingerprints && :query_fps"
    )
    if tenant_id is not None:
        # Own corpus plus the platform-level corpus (tenant_id IS NULL — the
        # bulk-loaded sources), which every tenant matches against.
        sql += " AND (tenant_id = :tenant_id OR tenant_id IS NULL)"
    if exclude_document_id is not None:
        sql += " AND document_id != :exclude_document_id"
    return sql + f" LIMIT {int(limit)}"


async def retrieve_l1_candidates(
    query_fingerprints: list[int],
    session: AsyncSession,
    *,
    top_k: int = 200,
    tenant_id: UUID | None = None,
    exclude_document_id: UUID | None = None,
) -> list[L1Candidate]:
    """Return top-K corpus chunks ranked by fingerprint-overlap cardinality.

    Parameters
    ----------
    query_fingerprints : list[int]
        Signed 64-bit fingerprints of the query chunk (output of
        `noplag_engine.fingerprinting.fingerprint`). An empty list
        short-circuits to an empty result without touching the database.
    session : AsyncSession
        SQLAlchemy async session bound to the engine database.
    top_k : int
        Maximum candidates to return after Python-side scoring (default
        200 — tune against PAN-PC-11 in later eval iterations).
    tenant_id : UUID | None
        When provided, restricts candidates to chunks belonging to that
        tenant. When None (admin / internal use), no tenant filter is
        applied.
    exclude_document_id : UUID | None
        When provided, chunks belonging to this document are excluded.
        Used by `match_against_corpus` so a freshly fingerprinted
        document doesn't match itself.

    Returns
    -------
    list[L1Candidate]
        Up to `top_k` candidates sorted by `score` descending, ties
        broken on `chunk_id` ascending. Empty when the query has no
        fingerprints or no chunks overlap.
    """
    if not query_fingerprints:
        return []

    # Probe only the rarest fingerprints (small posting lists -> small,
    # untruncated pool that contains the true source); score against the full
    # set below. Falls back to the full set when fingerprint_df is absent.
    df = await fetch_document_frequencies(session, query_fingerprints)
    if df is not None and not _is_discriminative(query_fingerprints, df):
        # All-common-phrase chunk: nothing discriminative to retrieve on (see
        # _NONDISCRIM_DF). Skip the posting-list wall and return no candidates.
        return []
    probe_fps = (
        query_fingerprints if df is None else _select_probe_fps(query_fingerprints, df)
    )

    # Shares the retrieve/score split with the batched path: candidate discovery
    # runs through _execute_candidates, scoring stays in Python below.
    rows = await _execute_candidates(
        session,
        probe_fps,
        tenant_id=tenant_id,
        exclude_document_id=exclude_document_id,
        limit=_BATCH_POOL_CAP,
    )

    query_set = set(query_fingerprints)
    candidates = [
        L1Candidate(
            chunk_id=row.id,
            document_id=row.document_id,
            score=len(query_set.intersection(row.fingerprints)),
            fingerprint_count=len(row.fingerprints),
        )
        for row in rows
    ]
    # The && predicate guarantees at least one shared fingerprint per row,
    # so every candidate has score >= 1 — no zero-score filter needed.
    candidates.sort(key=lambda c: (-c.score, c.chunk_id))
    return candidates[:top_k]


async def _execute_candidates(
    session: AsyncSession,
    query_fps: list[int],
    *,
    tenant_id: UUID | None,
    exclude_document_id: UUID | None,
    limit: int,
):
    stmt = text(
        _candidates_sql(
            tenant_id=tenant_id, exclude_document_id=exclude_document_id, limit=limit
        )
    ).bindparams(bindparam("query_fps", type_=ARRAY(BigInteger)))
    params: dict[str, object] = {"query_fps": query_fps}
    if tenant_id is not None:
        params["tenant_id"] = tenant_id
    if exclude_document_id is not None:
        params["exclude_document_id"] = exclude_document_id
    return (await session.execute(stmt, params)).all()


async def retrieve_l1_candidates_batched(
    chunk_fingerprints: list[list[int]],
    session: AsyncSession,
    *,
    top_k: int = 200,
    tenant_id: UUID | None = None,
    exclude_document_id: UUID | None = None,
    batch_size: int = _BATCH_SIZE,
    stop_fingerprints: frozenset[int] | None = None,
) -> list[list[L1Candidate]]:
    """Per-chunk top-K candidates, batching the corpus scan across chunks.

    Result shape matches calling `retrieve_l1_candidates` once per entry in
    `chunk_fingerprints`, but issues one corpus query per `batch_size` chunks
    instead of one per chunk — the win that keeps a large multi-chunk document
    off the per-query-I/O wall (see the module note). Returns a list parallel to
    `chunk_fingerprints`; entry i is that chunk's ranked candidates (empty when
    the chunk has no fingerprints or none overlap).

    Within a batch the corpus rows are fetched once over the union of the batch's
    fingerprints, then attributed back to each query chunk through an inverted
    fingerprint -> rows index, so a chunk is only scored against rows it can
    actually overlap (not the whole pool).

    `stop_fingerprints` (high-DF fingerprints) are dropped from the query side
    first. Batching alone only thins round-trips; the union of a batch's
    fingerprints still matches a huge row set when it contains corpus-wide
    fingerprints, so the per-batch scan stays expensive. Removing them shrinks
    the union's match set — that's what brings the per-batch cost down (and they
    carry no discriminative signal, so no recall cost).
    """
    n = len(chunk_fingerprints)
    if stop_fingerprints:
        chunk_fingerprints = [
            [f for f in fps if f not in stop_fingerprints] for fps in chunk_fingerprints
        ]
    results: list[list[L1Candidate]] = [[] for _ in range(n)]
    for start in range(0, n, batch_size):
        group = list(range(start, min(start + batch_size, n)))
        batch = await retrieve_l1_batch(
            group,
            chunk_fingerprints,
            session,
            top_k=top_k,
            tenant_id=tenant_id,
            exclude_document_id=exclude_document_id,
        )
        for i, cands in batch.items():
            results[i] = cands
    return results


async def retrieve_l1_batch(
    chunk_indices: list[int],
    chunk_fingerprints: list[list[int]],
    session: AsyncSession,
    *,
    top_k: int = 200,
    tenant_id: UUID | None = None,
    exclude_document_id: UUID | None = None,
) -> dict[int, list[L1Candidate]]:
    """One corpus scan for a group of query chunks; candidates per chunk.

    The single-batch primitive behind `retrieve_l1_candidates_batched` and the
    work-capped `run_check` loop: fetch corpus rows once over the union of the
    group's *rarest* fingerprints, then attribute them back to each chunk via an
    inverted fingerprint -> rows index and score against that chunk's own (full)
    fingerprints. Returns `{chunk_index: ranked candidates}` only for indices
    that have fingerprints and at least one overlap. `chunk_fingerprints` is
    assumed already stop-filtered by the caller.

    Retrieval probes only each chunk's `_RAREST_K` lowest-document-frequency
    fingerprints (via the `fingerprint_df` table): rare fingerprints have short
    posting lists, so the `&&` pool stays small and always contains the true
    source instead of being truncated by the row cap. Scoring still uses the
    chunk's full fingerprint set, so similarity is unchanged — only *which*
    fingerprints drive candidate discovery narrows. When `fingerprint_df` is
    absent (returns None), it falls back to the full-union `&&`.
    """
    group = [i for i in chunk_indices if chunk_fingerprints[i]]
    if not group:
        return {}
    _t = _now()
    all_fps = sorted({f for i in group for f in chunk_fingerprints[i]})
    df = await fetch_document_frequencies(session, all_fps)
    skipped = 0
    if df is None:
        # No DF table yet — fall back to the plain union over every fingerprint.
        probe_fps = {i: chunk_fingerprints[i] for i in group}
    else:
        # Drop non-discriminative chunks (all-common-phrase) before building the
        # union: their lone rare-ish fingerprint is the posting-list wall and they
        # yield no real single source. They simply produce no candidates.
        discriminative = [i for i in group if _is_discriminative(chunk_fingerprints[i], df)]
        skipped = len(group) - len(discriminative)
        group = discriminative
        probe_fps = {i: _select_probe_fps(chunk_fingerprints[i], df) for i in group}
    if not group:
        logger.debug(
            "L1_TIMING group=0 union_fps=0 rows=0 skipped=%d (all non-discriminative)",
            skipped,
        )
        return {}
    union = sorted({f for i in group for f in probe_fps[i]})
    _t_df = _now() - _t
    # The DF budget keeps each chunk's probe pool well under this; the cap only
    # guards a degenerate batch, and is high enough never to truncate a real match.
    pool = _BATCH_POOL_CAP
    _t = _now()
    rows = await _execute_candidates(
        session,
        union,
        tenant_id=tenant_id,
        exclude_document_id=exclude_document_id,
        limit=pool,
    )
    _t_exec = _now() - _t
    logger.debug(
        "L1_TIMING group=%d skipped=%d union_fps=%d rows=%d | df_select=%.2f &&_execute=%.2f",
        len(group),
        skipped,
        len(union),
        len(rows),
        _t_df,
        _t_exec,
    )
    if not rows:
        return {}

    union_set = set(union)
    fp_to_rows: dict[int, list[int]] = defaultdict(list)
    for ri, row in enumerate(rows):
        for f in row.fingerprints:
            if f in union_set:
                fp_to_rows[f].append(ri)

    out: dict[int, list[L1Candidate]] = {}
    for i in group:
        qset = set(chunk_fingerprints[i])
        cand_rows: set[int] = set()
        # Candidates are discovered through this chunk's rarest (probed)
        # fingerprints; they're then scored against the full set below.
        for f in probe_fps[i]:
            cand_rows.update(fp_to_rows.get(f, ()))
        if not cand_rows:
            continue
        scored = [
            L1Candidate(
                chunk_id=rows[ri].id,
                document_id=rows[ri].document_id,
                score=len(qset.intersection(rows[ri].fingerprints)),
                fingerprint_count=len(rows[ri].fingerprints),
            )
            for ri in cand_rows
        ]
        scored.sort(key=lambda c: (-c.score, c.chunk_id))
        out[i] = scored[:top_k]
    return out


def batch_visit_order(num_batches: int) -> list[int]:
    """Bit-reversal permutation of [0, num_batches).

    Drives the work-capped retrieval loop. Visiting batches in this order means
    that whenever the loop stops early on a time cap, the chunks it *did* process
    are spread uniformly across the document rather than clustered at the front —
    so partial coverage doesn't bias detection toward the document's opening.
    """
    if num_batches <= 1:
        return list(range(num_batches))
    bits = (num_batches - 1).bit_length()
    order: list[int] = []
    for i in range(1 << bits):
        r = 0
        x = i
        for _ in range(bits):
            r = (r << 1) | (x & 1)
            x >>= 1
        if r < num_batches:
            order.append(r)
    return order
