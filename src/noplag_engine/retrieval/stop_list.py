"""Canonical stop-fingerprint list — single source of truth for both layers.

High-document-frequency fingerprints ("stop fingerprints") occur in a huge
fraction of the corpus. The L1 `fingerprints && :query_fps` GIN scan over a
query carrying one of them touches a large slice of the corpus for no
discriminative gain, so both layers of the cascade drop them:

* the **query layer** (`workflows.check`) strips them from a query document's
  fingerprints before the `&&` probe;
* the **ingest layer** strips them before writing
  `chunks.fingerprints`, so they never enter the index in the first place.

For that to be sound the two layers MUST agree on the exact same set — a
fingerprint stopped at query time but written at ingest time (or vice versa)
leaves the index and the probe inconsistent. This module is the single place
both derive the set from, and it exposes a `content_hash` so each layer can
assert it loaded the same generation the other did (see `assert_hash_matches`).

Canonical source
----------------
The stop list is the **top-N fingerprints of `fingerprint_df` by document
frequency** (N via ``NOPLAG_STOP_LIST_N``, default 1000). `fingerprint_df`
is already the cascade's per-fingerprint DF table, rebuilt
after every major ingestion; deriving the stop list from it keeps one source of
truth instead of a separately-computed `stop_fingerprints` table that can drift.
The ``ORDER BY document_frequency DESC, fingerprint`` is fully deterministic, so
the same table generation + N always yields the same set and the same hash on
every reader.

Caching
-------
Loaded once and cached in process memory, keyed by N (or by file path for the
eval override). This is deliberate: the `content_hash` is meant to be stable for
a process's lifetime so the cross-layer assertion is cheap and meaningful. The
tradeoff is that a `fingerprint_df` rebuild does *not* go live until the process
restarts (or `load_stop_list(force_reload=True)` is called) — acceptable, since
the stop list only changes after a corpus re-fingerprint, which is itself a
restart-worthy event. Warm it at startup by calling `load_stop_list` once.

Eval override
-------------
``STOP_FINGERPRINTS_FILE`` (a newline-delimited file of bigint fingerprints)
short-circuits the DB read, so the eval harness can validate the filter against
a real stop list without writing a table to a scratch DB. The set still gets a
content hash, so file-driven and DB-driven runs are comparable.

Fallback
--------
When `fingerprint_df` is absent (CI, eval scratch, or a corpus that hasn't
built the table yet) the loader returns an empty stop list — the filter becomes
a no-op, exactly as before the
table exists.
"""

from __future__ import annotations

import hashlib
import logging
import os
import struct
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("noplag.retrieval.stop_list")

# How many of the highest-DF fingerprints to stop. The rarest-K probe and the
# non-discriminative-chunk skip in l1_winnowing already mask the high-DF tail for
# normal single-chunk queries; N is primarily a batched-union latency lever,
# tuned against the recall benchmark. Default kept at a conservative order of
# magnitude.
_DEFAULT_N = int(os.environ.get("NOPLAG_STOP_LIST_N", "1000"))

# Eval/validation override (kept under the original name for continuity with the
# eval harness): a newline-delimited file of bigint fingerprints.
_FILE_ENV = "STOP_FINGERPRINTS_FILE"


@dataclass(frozen=True)
class StopList:
    """An immutable stop-fingerprint set plus the hash that identifies it.

    `fingerprints` is the set to filter against; `content_hash` identifies the
    exact set (so two layers can confirm they loaded the same one without
    shipping the whole set around); `source` records where it came from for
    logs / debugging.
    """

    fingerprints: frozenset[int]
    content_hash: str
    source: str

    def __len__(self) -> int:
        return len(self.fingerprints)

    def __contains__(self, fingerprint: int) -> bool:
        return fingerprint in self.fingerprints


def content_hash(fingerprints: Iterable[int]) -> str:
    """Stable hash of a fingerprint set, independent of iteration order.

    Sorted then packed as signed 64-bit big-endian, so the digest depends only
    on the set's members — the canonical identity used for the cross-layer
    consistency check.
    """
    h = hashlib.blake2b(digest_size=16)
    for fp in sorted(set(fingerprints)):
        h.update(struct.pack(">q", fp))
    return h.hexdigest()


def _build(fingerprints: Iterable[int], source: str) -> StopList:
    fps = frozenset(int(f) for f in fingerprints)
    return StopList(fingerprints=fps, content_hash=content_hash(fps), source=source)


def _load_file(path: str) -> StopList:
    out: set[int] = set()
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#"):
                out.add(int(line))
    return _build(out, f"file:{path}")


_SELECT_TOP_N = text(
    "SELECT fingerprint FROM fingerprint_df "
    "ORDER BY document_frequency DESC, fingerprint "
    "LIMIT :n"
).bindparams(bindparam("n"))


async def _load_from_df(session: AsyncSession, n: int) -> StopList:
    # to_regclass returns NULL instead of erroring on an absent table, so the
    # probe can't abort the session's transaction (a failed SELECT would).
    present = (
        await session.execute(text("SELECT to_regclass('public.fingerprint_df')"))
    ).scalar()
    if present is None:
        return _build((), "empty")
    rows = (await session.execute(_SELECT_TOP_N, {"n": n})).scalars().all()
    return _build(rows, "fingerprint_df")


_cache: dict[tuple[str, object], StopList] = {}


async def load_stop_list(
    session: AsyncSession | None,
    *,
    n: int | None = None,
    force_reload: bool = False,
) -> StopList:
    """Return the canonical stop list, cached in process memory.

    `n` overrides ``NOPLAG_STOP_LIST_N`` for this call (both layers must pass the
    same `n`, or their hashes won't match — that's what `assert_hash_matches`
    catches). `force_reload` bypasses the cache to pick up a rebuilt
    `fingerprint_df` without a process restart. When ``STOP_FINGERPRINTS_FILE``
    is set, the file is the source and `session` may be None.
    """
    n = _DEFAULT_N if n is None else n
    path = os.environ.get(_FILE_ENV)
    key: tuple[str, object] = ("file", path) if path else ("df", n)
    if not force_reload and key in _cache:
        return _cache[key]

    if path:
        stop_list = _load_file(path)
    else:
        if session is None:
            raise ValueError("load_stop_list needs a session when STOP_FINGERPRINTS_FILE is unset")
        stop_list = await _load_from_df(session, n)

    _cache[key] = stop_list
    logger.info(
        "stop_list loaded source=%s size=%d hash=%s",
        stop_list.source,
        len(stop_list),
        stop_list.content_hash,
    )
    return stop_list


def assert_hash_matches(stop_list: StopList, expected_hash: str) -> None:
    """Raise if `stop_list` isn't the generation identified by `expected_hash`.

    The cross-layer guard: the ingest layer records the hash of the set it
    filtered with, and the query layer (or a startup check) asserts the set it
    loaded matches. A mismatch means the two layers are filtering on different
    stop sets — usually a different N, or one side reading a stale
    `fingerprint_df` generation.
    """
    if stop_list.content_hash != expected_hash:
        raise ValueError(
            "stop-list content hash mismatch: loaded "
            f"{stop_list.content_hash} (size {len(stop_list)}, source "
            f"{stop_list.source}) != expected {expected_hash}. The query and "
            "ingest layers are filtering on different stop sets; check that both "
            "pass the same N and read the same fingerprint_df generation."
        )


def clear_cache() -> None:
    """Drop the process cache (tests; or to force a reload across all keys)."""
    _cache.clear()
