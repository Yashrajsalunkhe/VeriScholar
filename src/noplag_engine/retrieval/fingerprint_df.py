"""Per-fingerprint document frequencies for rarest-K L1 retrieval.

L1 runs the `fingerprints && :query_fps` probe over only each query chunk's
*rarest* fingerprints, so the candidate pool stays small and always contains the
true source. To pick the
rarest, retrieval needs the document frequency of the query's fingerprints; this
module fetches it from the `fingerprint_df` table (built by
`scripts/compute_fingerprint_df.py`).

Only the *common* tail is stored (DF >= the build floor). A fingerprint absent
from the table is rarer than the floor — exactly the kind we prefer — so a
missing row reads as DF 0, the rarest possible.

`fetch_document_frequencies` returns `None` (not an empty dict) when the table
does not exist, so the caller can fall back to the plain union `&&` on a DB that
hasn't built the table yet (CI, eval scratch, or a freshly-provisioned corpus). An *empty* table
(present but unbuilt) is indistinguishable from "all rare", which is the safe
default. Read per call, like the stop-fingerprint set, so a rebuilt table goes
live with no engine restart.
"""

from __future__ import annotations

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.types import BigInteger


async def fetch_document_frequencies(
    session: AsyncSession, fingerprints: list[int]
) -> dict[int, int] | None:
    """DF for each of `fingerprints` present in the table.

    Returns a `{fingerprint: document_frequency}` dict (fingerprints absent from
    the table are simply not keyed — the caller treats them as DF 0). Returns
    `None` when the `fingerprint_df` table does not exist, signalling the caller
    to fall back to non-rarest-K retrieval.
    """
    if not fingerprints:
        return {}

    # to_regclass returns NULL instead of erroring on an absent table, so the
    # probe can't abort the session's transaction.
    present = (
        await session.execute(text("SELECT to_regclass('public.fingerprint_df')"))
    ).scalar()
    if present is None:
        return None

    stmt = text(
        "SELECT fingerprint, document_frequency FROM fingerprint_df "
        "WHERE fingerprint = ANY(:fps)"
    ).bindparams(bindparam("fps", type_=ARRAY(BigInteger)))
    rows = (await session.execute(stmt, {"fps": fingerprints})).all()
    return {int(r.fingerprint): int(r.document_frequency) for r in rows}
