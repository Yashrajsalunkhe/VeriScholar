"""Transparent hybrid scoring and ranking.

Combines evidence from three detection layers into a single ranked report:

- **Exact match** (L0/L1 winnowing) — verbatim text overlap
- **Fuzzy match** (L2 token/n-gram) — near-verbatim modifications
- **Semantic match** (L3 embeddings) — paraphrase candidates

Design principles:
- Weights are configurable and documented — no opaque "AI score"
- Similarity ≠ plagiarism — the system reports evidence, not verdicts
- Each layer's contribution is reported separately for transparency
- Shared terminology and independently written descriptions of the same
  concept are not automatically classified as plagiarism
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID


@dataclass(frozen=True)
class MatchEvidence:
    """Evidence from a single detection layer for one source."""
    match_type: str  # "exact", "fuzzy", "semantic"
    similarity: float  # 0–1, layer-specific
    matched_chars: int = 0
    passage_count: int = 0
    # Representative passage excerpts (for the report)
    excerpts: tuple[str, ...] = ()


@dataclass
class HybridMatch:
    """A source document with combined evidence from all layers."""
    source_document_id: UUID
    source_title: str | None = None
    source_doi: str | None = None
    source_url: str | None = None
    source_authors: list[str] = field(default_factory=list)
    source_year: int | None = None

    # Per-layer evidence (each layer may or may not have found this source)
    exact_evidence: MatchEvidence | None = None
    fuzzy_evidence: MatchEvidence | None = None
    semantic_evidence: MatchEvidence | None = None

    # Combined scores
    combined_score: float = 0.0
    exact_similarity_pct: float = 0.0
    fuzzy_similarity_pct: float = 0.0
    semantic_similarity_pct: float = 0.0

    # Transparency
    score_breakdown: dict = field(default_factory=dict)


@dataclass
class HybridReport:
    """Complete hybrid analysis report."""
    query_text_length: int
    total_exact_matched_chars: int = 0
    overall_exact_similarity_pct: float = 0.0
    overall_fuzzy_similarity_pct: float = 0.0
    overall_semantic_similarity_pct: float = 0.0

    # Ranked sources with evidence from all layers
    sources: list[HybridMatch] = field(default_factory=list)

    # Layer summary
    exact_sources_count: int = 0
    fuzzy_sources_count: int = 0
    semantic_candidates_count: int = 0

    # Coverage and warnings
    coverage: str = "full"
    warnings: list[str] = field(default_factory=list)

    # Layer weights used (for report transparency)
    weights: dict = field(default_factory=dict)


def compute_hybrid_scores(
    sources: list[HybridMatch],
    *,
    exact_weight: float = 0.50,
    fuzzy_weight: float = 0.25,
    semantic_weight: float = 0.25,
) -> list[HybridMatch]:
    """Compute combined scores for each source using configurable weights.

    Each layer contributes its similarity score × weight to the combined
    score.  Only layers with evidence contribute; missing layers do not
    penalize the score — their weight is redistributed proportionally
    to active layers.

    The combined score is NOT a plagiarism probability.  It is a
    transparent weighted similarity indicator.
    """
    for source in sources:
        # Collect active layer scores
        active: list[tuple[str, float, float]] = []
        if source.exact_evidence is not None:
            active.append(("exact", source.exact_evidence.similarity, exact_weight))
        if source.fuzzy_evidence is not None:
            active.append(("fuzzy", source.fuzzy_evidence.similarity, fuzzy_weight))
        if source.semantic_evidence is not None:
            active.append(("semantic", source.semantic_evidence.similarity, semantic_weight))

        if not active:
            source.combined_score = 0.0
            continue

        # Redistribute weights proportionally among active layers
        total_weight = sum(w for _, _, w in active)
        if total_weight == 0:
            source.combined_score = 0.0
            continue

        combined = 0.0
        breakdown: dict[str, float] = {}
        for layer_name, score, weight in active:
            effective_weight = weight / total_weight
            contribution = score * effective_weight
            combined += contribution
            breakdown[layer_name] = round(contribution, 4)
            breakdown[f"{layer_name}_raw"] = round(score, 4)
            breakdown[f"{layer_name}_weight"] = round(effective_weight, 4)

        source.combined_score = round(combined, 4)
        source.score_breakdown = breakdown

        # Set per-layer percentages for display
        if source.exact_evidence:
            source.exact_similarity_pct = round(
                source.exact_evidence.similarity * 100, 2
            )
        if source.fuzzy_evidence:
            source.fuzzy_similarity_pct = round(
                source.fuzzy_evidence.similarity * 100, 2
            )
        if source.semantic_evidence:
            source.semantic_similarity_pct = round(
                source.semantic_evidence.similarity * 100, 2
            )

    # Sort by combined score descending
    sources.sort(key=lambda s: -s.combined_score)
    return sources
