"""L2 fuzzy alignment — chunk-level near-verbatim detection.

Sits between L0/L1 (exact winnowing + seed-extend) and the L3 semantic
layer, and closes a concrete, observable gap in the exact-only pipeline:

    A query chunk that was *lightly paraphrased* from a source — a few
    synonym substitutions, some word reordering, filler words inserted —
    still shares enough winnowing fingerprints with that source for L1
    retrieval to surface it as a candidate. But seed-extend needs a
    `min_seed_len` (30-char) *exact* run to seed a passage, and once edits
    land every few words no such run survives. The candidate reaches
    alignment, produces zero verbatim passages, and the source silently
    drops out of the report — the check returns 0% on text that is plainly
    derived from the corpus.

`fuzzy_align` is run on exactly those survivors: L1 candidates for a query
chunk that seed-extend did **not** already explain with a verbatim passage.
The source is provably in the candidate pool (it shared fingerprints), so
this adds recall without widening retrieval. For each such candidate it
computes a combined token/​n-gram/​LCS similarity (see ``noplag_engine.fuzzy``)
and, when the score clears ``threshold``, emits one ``AlignedPassage`` with
``match_type="fuzzy"`` spanning the query chunk.

Why whole-chunk spans
---------------------
Fuzzy similarity is a chunk-to-chunk measure — it does not localise a
character range the way exact seed-extend does. Rather than invent
offsets, a fuzzy passage attributes the **whole query chunk** (its
char range) to the source, which is the honest granularity of the signal:
"this chunk reads as a paraphrase of that source chunk." Downstream
interval-merging treats these spans exactly like verbatim ones, and the
``match_type`` field lets the report separate the two kinds of evidence.

This stage is pure-Python and dependency-free; it is deliberately kept out
of the hot L1 scan and run only on the already-narrowed candidate set.
"""

from __future__ import annotations

from uuid import UUID

from noplag_engine.alignment import AlignedPassage
from noplag_engine.fuzzy import combined_fuzzy_score

# Default minimum combined fuzzy score for a candidate to be reported.
#
# Chosen from the measured score distribution (see test_fuzzy_align and the PR
# notes). On a representative sentence group the combined metric separates the
# cases cleanly:
#
#     identical                 1.00
#     light edit (few synonyms) 0.82   (seed-extend already catches these)
#     moderate paraphrase       0.45   <- the recall fuzzy exists to add
#     heavy paraphrase          0.13   ┐ statistically indistinguishable — this
#     independent, same topic   0.14   ┘ is the false-positive floor
#     unrelated                 0.06
#
# 0.4 sits in the only safe window: above the ~0.14 floor where independent
# writing on the same subject lives (so honest work is not flagged), below the
# ~0.45 moderate-paraphrase signal (so real light paraphrase is caught). Heavy
# paraphrase is deliberately *not* chased here — it cannot be told apart from
# independent prose by surface token/n-gram overlap; that is the L3 semantic
# layer's job, not L2's. A candidate only reaches this stage because it already
# shared rare winnowing fingerprints with the query chunk, which further biases
# the pool toward genuine derivations.
DEFAULT_FUZZY_THRESHOLD = 0.4

# Candidate chunks far longer or shorter than the query chunk cannot be a
# near-verbatim paraphrase of it — a 4-sentence query chunk is not a fuzzy
# match for a 40-sentence candidate even if the short one is a substring-ish
# subset. Skip pairs whose length ratio is outside this band before paying for
# the (more expensive) LCS term. Guards both runtime and precision.
_LENGTH_RATIO_FLOOR = 0.5
_LENGTH_RATIO_CEIL = 2.0


def fuzzy_align(
    query_chunk_id: UUID,
    query_text: str,
    candidates: list[tuple[UUID, str]],
    *,
    threshold: float = DEFAULT_FUZZY_THRESHOLD,
    min_chunk_len: int = 50,
) -> list[AlignedPassage]:
    """Fuzzy-match `query_text` against each candidate chunk.

    Parameters
    ----------
    query_chunk_id : UUID
        Identifier carried through to emitted passages (the report layer
        attributes the query side of each match with it), exactly as in
        :func:`noplag_engine.alignment.align`.
    query_text : str
        Plain text of the query chunk. Emitted passages span ``[0, len)``
        in these coordinates; the check workflow translates them to
        full-query offsets via the chunk's ``char_start``.
    candidates : list[tuple[UUID, str]]
        ``(chunk_id, text)`` pairs — the L1 survivors for this query chunk
        that seed-extend produced no verbatim passage for.
    threshold : float
        Minimum combined fuzzy score (0–1) to emit a passage.
    min_chunk_len : int
        Skip query/candidate texts shorter than this many characters;
        fuzzy similarity on very short strings is noisy.

    Returns
    -------
    list[AlignedPassage]
        One passage per candidate clearing the threshold, each spanning the
        whole query chunk, ``match_type="fuzzy"``, ``score`` = the combined
        fuzzy similarity. Sorted by ``(-score, candidate_chunk_id)`` so the
        strongest paraphrase evidence leads. Empty when the query is too
        short or no candidate clears the threshold.
    """
    if not query_text or not candidates:
        return []
    q_len = len(query_text)
    if q_len < min_chunk_len:
        return []

    scored: list[tuple[float, UUID, int]] = []
    for candidate_chunk_id, candidate_text in candidates:
        c_len = len(candidate_text)
        if c_len < min_chunk_len:
            continue
        ratio = q_len / c_len
        if ratio < _LENGTH_RATIO_FLOOR or ratio > _LENGTH_RATIO_CEIL:
            continue
        score = combined_fuzzy_score(query_text, candidate_text)
        if score >= threshold:
            scored.append((score, candidate_chunk_id, c_len))

    scored.sort(key=lambda t: (-t[0], t[1]))

    return [
        AlignedPassage(
            query_chunk_id=query_chunk_id,
            candidate_chunk_id=candidate_chunk_id,
            query_start=0,
            query_end=q_len,
            candidate_start=0,
            candidate_end=c_len,
            score=round(score, 4),
            match_type="fuzzy",
        )
        for score, candidate_chunk_id, c_len in scored
    ]
