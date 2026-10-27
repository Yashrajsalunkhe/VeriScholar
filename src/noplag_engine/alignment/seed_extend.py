"""L0 text alignment — seed-and-extend over query/candidate chunk pairs.

Final precision stage of the four-layer retrieval cascade.
L1/L2/L3 produce candidate chunks; L0 turns
each (query, candidate) pair into the character-offset-bearing passages
the Report renders.

BLAST-adapted for text
----------------------
The shape is borrowed from Altschul et al. (1990, BLAST) but adapted for
natural language rather than biological sequences:

1. **Seed** — find every maximal exact common substring of length
   ≥ `min_seed_len` between query and candidate. Maximal means the
   substring cannot be extended in either direction without introducing
   a mismatch. Implemented by indexing the candidate's `min_seed_len`
   windows in a Python dict (hash-keyed lookup; verification is exact
   string equality, so hash collisions cost CPU but do not affect
   output).

2. **Extend** — from each seed's boundaries, walk outward in 20-char
   lookahead windows. The boundary advances by the lookahead step when
   the per-window match ratio meets `extend_tolerance`; otherwise the
   walk stops. This is intentionally simpler than full Smith-Waterman
   gap-tolerant alignment — it handles single-character substitutions
   (the dominant edit type in lightly-paraphrased plagiarism) but not
   insertions/deletions. If eval surfaces gap-sensitive misses, swap in
   SW then.

3. **Merge + filter** — multiple seeds along the same diagonal (same
   `c_start - q_start`) often extend across each other; merged passages
   replace the overlapping pieces. After merging, passages shorter than
   `min_passage_len` are dropped — those are below the report-worthy
   threshold even if mathematically real.

Score per emitted passage is `matching_chars / max(q_len, c_len)` over
the aligned region. Since the extend stage advances query and candidate
together in equal-sized steps, `q_len == c_len`; the `max()` form is
preserved for forward-compatibility with gap-tolerant alignment.

Output is sorted by `(query_chunk_id, query_start, candidate_chunk_id)`
for deterministic ordering downstream.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from uuid import UUID

# Lookahead width for the tolerant-extend stage (chars). Small enough to
# react quickly when the underlying text actually diverges, large enough
# that a couple of substituted chars don't kill the window.
_EXTEND_LOOKAHEAD = 20

# When merging passages along the same diagonal, treat query ranges
# within this many chars of each other as adjoining. The tolerant-extend
# stage walks across short mismatch runs, so adjacent same-diagonal seeds
# usually already overlap by the time merge runs — this gap covers the
# residual case of a sub-`_EXTEND_LOOKAHEAD` mismatch run that stopped
# both extensions just shy of meeting.
_MERGE_GAP = 20

# When a single (query, candidate) pair yields multiple maximal exact
# matches whose query ranges overlap — typical when either side of the
# pair contains self-repeating text — the report layer wants one
# attribution per query region rather than every cross-pairing. After
# merge, keep the longest passage and drop any later passage whose query
# range overlaps an already-kept one by more than this fraction.
_QUERY_OVERLAP_DEDUPE_THRESHOLD = 0.5


@dataclass(frozen=True)
class AlignedPassage:
    """A single aligned passage between a query chunk and a candidate chunk.

    Offset coordinate systems
    -------------------------
    `query_start` / `query_end` index into the **query chunk text** passed
    to `align()`. The check workflow translates these to
    full-query coordinates via `chunk.char_start` before assembling the
    report; if you call `align()` directly, you're responsible for the
    same translation.

    `candidate_start` / `candidate_end` index into the **candidate chunk
    text** — not the full source document. Consumers that need full-
    source offsets (e.g. the Report UI rendering passage highlights
    against the original document text) must join through the chunks
    table: `chunks.char_start + passage.candidate_start`.
    """

    query_chunk_id: UUID
    candidate_chunk_id: UUID
    query_start: int
    query_end: int
    candidate_start: int
    candidate_end: int
    score: float
    # Match kind, so downstream consumers can distinguish alignment sources
    # once softer detection layers exist. Always "verbatim" for L0/L1
    # seed-extend alignments.
    match_type: str = "verbatim"


@dataclass(frozen=True)
class _Seed:
    q_start: int
    q_end: int
    c_start: int
    c_end: int


def align(
    query_chunk_id: UUID,
    query_text: str,
    candidates: list[tuple[UUID, str]],
    *,
    min_seed_len: int = 30,
    min_passage_len: int = 50,
    extend_tolerance: float = 0.85,
) -> list[AlignedPassage]:
    """Align `query_text` against each candidate; return passage spans.

    Parameters
    ----------
    query_chunk_id : UUID
        Identifier carried through to the emitted passages (used by the
        report layer to attribute the query side of each match).
    query_text : str
        Plain text of the query chunk. Char offsets in the returned
        passages index into this string directly.
    candidates : list[tuple[UUID, str]]
        L1 (and later L2/L3) survivors. Each entry is `(chunk_id, text)`;
        the chunk_id is carried through to the emitted passages.
    min_seed_len : int
        Minimum length of an exact common substring to seed extension
        (default 30 — tune in future eval iterations against
        PAN-PC-11).
    min_passage_len : int
        Minimum query-side length of an emitted passage (default 50 —
        tune in future eval iterations against PAN-PC-11).
        Passages shorter than this after merge are dropped.
    extend_tolerance : float
        Minimum match ratio in the extend-stage lookahead window
        (default 0.85 — tune in future eval iterations against
        PAN-PC-11). A lower value tolerates more paraphrasing at the
        cost of false positives over loosely-similar prose.

    Returns
    -------
    list[AlignedPassage]
        Sorted by `(query_chunk_id, query_start, candidate_chunk_id)`.
        Empty when the query is shorter than `min_seed_len`, no
        candidates are provided, or no candidate yields a passage that
        survives merge + filter.
    """
    if not query_text or not candidates:
        return []
    if len(query_text) < min_seed_len:
        return []

    passages: list[AlignedPassage] = []
    for candidate_chunk_id, candidate_text in candidates:
        if len(candidate_text) < min_seed_len:
            continue
        seeds = _find_seeds(query_text, candidate_text, min_seed_len)
        if not seeds:
            continue
        extended = [
            _extend(seed, query_text, candidate_text, extend_tolerance)
            for seed in seeds
        ]
        merged = _merge(extended)
        deduped = _dedupe_by_query_coverage(merged)
        for span in deduped:
            q_len = span.q_end - span.q_start
            if q_len < min_passage_len:
                continue
            passages.append(
                _score_and_build(
                    span,
                    query_chunk_id,
                    candidate_chunk_id,
                    query_text,
                    candidate_text,
                )
            )

    passages.sort(
        key=lambda p: (
            p.query_chunk_id,
            p.query_start,
            p.candidate_chunk_id,
        )
    )
    return passages


def _find_seeds(query: str, candidate: str, min_seed_len: int) -> list[_Seed]:
    """Return every maximal exact common substring of length >= min_seed_len.

    Hash-indexed seeding: every `min_seed_len`-window of the candidate is
    placed in a dict, then query windows look up that dict. On a hit, the
    match is extended bidirectionally to its maximal exact extent.
    Maximal matches are deduped on (q_start, c_start) so re-discovery
    from neighbouring windows is cheap (set lookup) rather than redundant
    extension work.
    """
    cand_index: dict[str, list[int]] = defaultdict(list)
    for i in range(len(candidate) - min_seed_len + 1):
        cand_index[candidate[i : i + min_seed_len]].append(i)

    seen: set[tuple[int, int]] = set()
    seeds: list[_Seed] = []

    qi = 0
    while qi <= len(query) - min_seed_len:
        window = query[qi : qi + min_seed_len]
        positions = cand_index.get(window)
        if not positions:
            qi += 1
            continue
        longest = 0
        for c_pos in positions:
            left = _extend_exact_left(query, candidate, qi, c_pos)
            right = _extend_exact_right(
                query, candidate, qi + min_seed_len, c_pos + min_seed_len
            )
            q_start = qi - left
            c_start = c_pos - left
            key = (q_start, c_start)
            if key in seen:
                longest = max(longest, min_seed_len + left + right)
                continue
            seen.add(key)
            seeds.append(
                _Seed(
                    q_start=q_start,
                    q_end=q_start + min_seed_len + left + right,
                    c_start=c_start,
                    c_end=c_start + min_seed_len + left + right,
                )
            )
            longest = max(longest, min_seed_len + left + right)
        # Skip past the just-discovered maximal match. Other maximal
        # matches that *start* inside this skip range cannot exist at the
        # same diagonal — they'd be sub-matches of the one we just found.
        # Different-diagonal maximal matches starting inside this range
        # are still found because their c_pos differs; this loop iterates
        # over every c_pos for the current window, so they were emitted
        # already.
        qi += max(1, longest - min_seed_len + 1)
    return seeds


def _extend_exact_left(query: str, candidate: str, qi: int, ci: int) -> int:
    """Maximal exact extension to the left of (qi, ci). Returns char count."""
    n = 0
    while qi - n - 1 >= 0 and ci - n - 1 >= 0 and query[qi - n - 1] == candidate[ci - n - 1]:
        n += 1
    return n


def _extend_exact_right(query: str, candidate: str, qi: int, ci: int) -> int:
    """Maximal exact extension to the right of (qi, ci). Returns char count."""
    n = 0
    while qi + n < len(query) and ci + n < len(candidate) and query[qi + n] == candidate[ci + n]:
        n += 1
    return n


def _extend(
    seed: _Seed,
    query: str,
    candidate: str,
    tolerance: float,
) -> _Seed:
    """Tolerant lookahead extension on both sides of a maximal exact seed.

    Boundaries advance by `_EXTEND_LOOKAHEAD` chars per step while the
    per-window match ratio meets `tolerance`. Both sides walk together,
    so q_end - q_start always equals c_end - c_start.
    """
    q_end, c_end = seed.q_end, seed.c_end
    while q_end + _EXTEND_LOOKAHEAD <= len(query) and c_end + _EXTEND_LOOKAHEAD <= len(candidate):
        q_window = query[q_end : q_end + _EXTEND_LOOKAHEAD]
        c_window = candidate[c_end : c_end + _EXTEND_LOOKAHEAD]
        if _match_ratio(q_window, c_window) >= tolerance:
            q_end += _EXTEND_LOOKAHEAD
            c_end += _EXTEND_LOOKAHEAD
        else:
            break

    q_start, c_start = seed.q_start, seed.c_start
    while q_start - _EXTEND_LOOKAHEAD >= 0 and c_start - _EXTEND_LOOKAHEAD >= 0:
        q_window = query[q_start - _EXTEND_LOOKAHEAD : q_start]
        c_window = candidate[c_start - _EXTEND_LOOKAHEAD : c_start]
        if _match_ratio(q_window, c_window) >= tolerance:
            q_start -= _EXTEND_LOOKAHEAD
            c_start -= _EXTEND_LOOKAHEAD
        else:
            break

    return _Seed(q_start=q_start, q_end=q_end, c_start=c_start, c_end=c_end)


def _match_ratio(a: str, b: str) -> float:
    return sum(1 for x, y in zip(a, b, strict=True) if x == y) / len(a)


def _merge(passages: list[_Seed]) -> list[_Seed]:
    """Merge passages along the same diagonal whose query ranges adjoin.

    Two passages are merged only when they sit on the exact same diagonal
    (`c_start - q_start` equal) AND their query ranges overlap or sit
    within `_MERGE_GAP` chars of each other. Same-diagonal means the two
    seeds describe a single continuous shared run with a short mismatch
    interruption in the middle. Different-diagonal seeds that happen to
    abut in query space describe *different* shared runs — merging them
    would synthesize an alignment whose middle doesn't actually align.
    """
    if not passages:
        return []
    sorted_passages = sorted(passages, key=lambda p: (p.q_start, p.c_start))
    merged: list[_Seed] = [sorted_passages[0]]
    for current in sorted_passages[1:]:
        last = merged[-1]
        same_diagonal = (current.c_start - current.q_start) == (
            last.c_start - last.q_start
        )
        overlap_or_adjacent = current.q_start <= last.q_end + _MERGE_GAP
        if same_diagonal and overlap_or_adjacent:
            merged[-1] = _Seed(
                q_start=last.q_start,
                q_end=max(last.q_end, current.q_end),
                c_start=last.c_start,
                c_end=max(last.c_end, current.c_end),
            )
        else:
            merged.append(current)
    return merged


def _dedupe_by_query_coverage(passages: list[_Seed]) -> list[_Seed]:
    """Drop passages whose query range overlaps a longer passage.

    Self-repetition in either side of the pair (a phrase that appears
    twice in the source, for example) makes the seed stage emit several
    maximal exact matches over the same query region but at different
    candidate offsets. From the report's perspective, that's noise: the
    reader wants one attribution per query span, not the full cross
    product. Keep the longest match per query region.
    """
    by_length_desc = sorted(passages, key=lambda p: -(p.q_end - p.q_start))
    kept: list[_Seed] = []
    for current in by_length_desc:
        current_len = current.q_end - current.q_start
        if current_len <= 0:
            continue
        subsumed = False
        for k in kept:
            overlap_start = max(current.q_start, k.q_start)
            overlap_end = min(current.q_end, k.q_end)
            overlap = max(0, overlap_end - overlap_start)
            if overlap / current_len > _QUERY_OVERLAP_DEDUPE_THRESHOLD:
                subsumed = True
                break
        if not subsumed:
            kept.append(current)
    return kept


def _score_and_build(
    span: _Seed,
    query_chunk_id: UUID,
    candidate_chunk_id: UUID,
    query_text: str,
    candidate_text: str,
) -> AlignedPassage:
    q_segment = query_text[span.q_start : span.q_end]
    c_segment = candidate_text[span.c_start : span.c_end]
    # Extend stage advances both sides in lock-step, so the segments have
    # identical length here. zip strict=False keeps the score formula
    # robust if a future gap-tolerant extender lets the two segments
    # diverge in length without rippling assertions through this scorer.
    matches = sum(1 for a, b in zip(q_segment, c_segment) if a == b)  # noqa: B905
    aligned_len = max(len(q_segment), len(c_segment))
    score = matches / aligned_len if aligned_len else 0.0
    return AlignedPassage(
        query_chunk_id=query_chunk_id,
        candidate_chunk_id=candidate_chunk_id,
        query_start=span.q_start,
        query_end=span.q_end,
        candidate_start=span.c_start,
        candidate_end=span.c_end,
        score=score,
    )
