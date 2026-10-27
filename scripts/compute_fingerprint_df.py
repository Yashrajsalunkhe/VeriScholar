"""Build the per-fingerprint document-frequency table from the corpus.

L1 retrieval picks each query chunk's *rarest* fingerprints (small posting
lists) for the `&&` probe so the candidate pool stays small and always
contains the true source. That needs the document frequency of each
fingerprint, which this computes: a full `unnest(fingerprints)` + group-by
over `chunks`, kept above a frequency floor (the rare tail is left out and
treated as DF 0 by the engine — exactly the fingerprints we *want* to
prefer).

This is a genuine full aggregate over every chunk — hours on a very large
corpus. It is built into a shadow table and swapped in atomically, so the
engine keeps serving from the old table (or no table at all on first run)
until the new one is ready. The PK is added after the bulk load, not during,
so the load isn't paying btree-maintenance per row.

    DATABASE_URL=... python scripts/compute_fingerprint_df.py --floor 200 --work-mem 2GB

Only worth running once the corpus is large enough that L1 latency matters
(tens of millions of chunks); the engine falls back to full-union retrieval
when the table is absent. Re-run after each major ingestion. DATABASE_URL
must point at a role that can CREATE / DROP tables. Safe to run alongside
the engine API — it reads `chunks` and writes a separate table, no locks on
live data — but it saturates disk I/O, so checks run slow while it cooks.
"""

from __future__ import annotations

import argparse
import os
import time

from sqlalchemy import create_engine, text

# A fingerprint below this document frequency is "rare" — discriminative and
# cheap to probe — so the engine prefers it and we don't need to store it. We
# only store the common tail (DF >= floor) the engine must *avoid*. 200 on a
# 100M-chunk corpus keeps the rarest-K `&&` pool to ~K*200 rows worst case.
DEFAULT_FLOOR = 200

_BUILD = "fingerprint_df_build"
_LIVE = "fingerprint_df"


def _run(conn, sql: str, **params) -> None:
    conn.execute(text(sql), params or None)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--floor", type=int, default=DEFAULT_FLOOR,
                    help=f"store fingerprints with DF >= this (default {DEFAULT_FLOOR})")
    ap.add_argument("--work-mem", default="2GB",
                    help="session work_mem for the aggregate (default 2GB)")
    ap.add_argument("--max-parallel", type=int, default=4,
                    help="max_parallel_workers_per_gather for the aggregate (default 4)")
    ap.add_argument("--analyze", action="store_true",
                    help="ANALYZE the new table after the swap")
    args = ap.parse_args()

    dsn = os.environ.get(
        "DATABASE_URL", "postgresql+psycopg://noplag:noplag_dev@localhost:5432/noplag"
    )
    eng = create_engine(dsn)

    t0 = time.monotonic()
    # AUTOCOMMIT: the aggregate is one long statement; keeping it out of an open
    # transaction avoids holding a snapshot / bloating for hours.
    with eng.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        _run(conn, f"SET work_mem = '{args.work_mem}'")
        _run(conn, f"SET max_parallel_workers_per_gather = {int(args.max_parallel)}")

        print(f"building {_BUILD} (floor DF >= {args.floor}) ...", flush=True)
        _run(conn, f"DROP TABLE IF EXISTS {_BUILD}")
        _run(
            conn,
            f"CREATE TABLE {_BUILD} ("
            "  fingerprint BIGINT NOT NULL,"
            "  document_frequency INTEGER NOT NULL,"
            "  computed_at TIMESTAMPTZ NOT NULL DEFAULT now()"
            ")",
        )
        _run(
            conn,
            f"INSERT INTO {_BUILD} (fingerprint, document_frequency) "
            "SELECT fp, count(*)::int "
            "FROM (SELECT unnest(fingerprints) AS fp FROM chunks) u "
            "GROUP BY fp HAVING count(*) >= :floor",
            floor=args.floor,
        )
        rows = conn.execute(text(f"SELECT count(*) FROM {_BUILD}")).scalar()
        print(f"  aggregated {rows:,} fingerprints in {time.monotonic() - t0:.0f}s", flush=True)

        print("  adding primary key ...", flush=True)
        _run(conn, f"ALTER TABLE {_BUILD} ADD PRIMARY KEY (fingerprint)")

    # Atomic swap in a real transaction so readers see the old or the new table,
    # never a missing one.
    with eng.begin() as tx:
        _run(tx, f"DROP TABLE IF EXISTS {_LIVE}")
        _run(tx, f"ALTER TABLE {_BUILD} RENAME TO {_LIVE}")
        _run(tx, f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_LIVE} TO noplag_app")
    print(f"swapped {_BUILD} -> {_LIVE}", flush=True)

    if args.analyze:
        with eng.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            _run(conn, f"ANALYZE {_LIVE}")
        print(f"analyzed {_LIVE}", flush=True)

    print(f"done in {time.monotonic() - t0:.0f}s ({rows:,} rows)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
