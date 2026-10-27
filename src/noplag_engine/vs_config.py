"""VeriScholar configuration — branding, scholarly APIs, semantic pipeline.

Extends the base Noplag fingerprint config with settings for the scholarly
ingestion pipeline, semantic retrieval, hybrid scoring, and configurable
branding.  All settings are environment-variable-driven with sensible
defaults so a bare ``docker compose up`` still works.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class BrandingConfig:
    """Configurable product branding — change without code edits."""
    product_name: str = "VeriScholar AI"
    product_tagline: str = "Open-Source Research Plagiarism Detection"
    product_url: str = ""
    # Controls the <title>, report headers, and API metadata.
    # Original Noplag Engine branding is preserved in attribution.


@dataclass(frozen=True)
class ScholarlyAPIConfig:
    """API keys and rate limits for scholarly data sources."""
    # OpenAlex — free, polite-pool with email
    openalex_email: str = ""
    openalex_max_per_second: float = 10.0

    # arXiv — free, rate-limited (see arXiv API TOS)
    arxiv_max_per_second: float = 1.0

    # CORE — requires API key for bulk access
    core_api_key: str = ""
    core_max_per_second: float = 5.0

    # PubMed Central — NCBI E-utilities
    pubmed_email: str = ""
    pubmed_api_key: str = ""
    pubmed_max_per_second: float = 3.0

    # Crossref — free, polite-pool with email
    crossref_email: str = ""
    crossref_max_per_second: float = 10.0


@dataclass(frozen=True)
class SemanticConfig:
    """Sentence Transformer / FAISS settings."""
    # Model name from HuggingFace hub — must support passage embeddings.
    model_name: str = "all-MiniLM-L6-v2"
    # Embedding dimension (must match the model).
    embedding_dim: int = 384
    # Device: "cpu", "cuda", or "auto" (auto-detect GPU).
    device: str = "cpu"
    # FAISS index type: "flat" (exact), "ivf" (approximate, faster).
    faiss_index_type: str = "flat"
    # Number of IVF cells (only for ivf index type).
    faiss_nlist: int = 100
    # Top-K candidates from FAISS retrieval.
    semantic_top_k: int = 50
    # Minimum cosine similarity to consider a semantic candidate.
    semantic_threshold: float = 0.65
    # Enable cross-encoder reranking on semantic candidates.
    use_cross_encoder: bool = False
    cross_encoder_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    cross_encoder_top_k: int = 10


@dataclass(frozen=True)
class HybridScoringConfig:
    """Weights for the transparent hybrid scoring mechanism."""
    # Individual weights (must sum to 1.0 when all enabled).
    exact_match_weight: float = 0.50
    fuzzy_match_weight: float = 0.25
    semantic_match_weight: float = 0.25
    # Minimum passage length to include in scoring.
    min_passage_chars: int = 50
    # Whether to include each detection layer.
    enable_exact: bool = True
    enable_fuzzy: bool = True
    enable_semantic: bool = True


@dataclass(frozen=True)
class SecurityConfig:
    """Security settings for the platform."""
    # JWT secret — MUST be changed in production.
    jwt_secret: str = "CHANGE_ME_IN_PRODUCTION"
    jwt_algorithm: str = "HS256"
    jwt_expiry_hours: int = 24
    # Max upload size in bytes (default 50 MiB for research papers).
    max_upload_bytes: int = 50 * 1024 * 1024
    # Rate limiting (requests per minute per IP).
    rate_limit_rpm: int = 60
    # CORS origins (comma-separated).
    cors_origins: str = "http://localhost:3000,http://localhost:5173"
    # Enable authentication (False = open access, for dev only).
    auth_enabled: bool = False


@dataclass(frozen=True)
class VeriScholarConfig:
    """Top-level config aggregating all VeriScholar subsystems."""
    branding: BrandingConfig = field(default_factory=BrandingConfig)
    scholarly: ScholarlyAPIConfig = field(default_factory=ScholarlyAPIConfig)
    semantic: SemanticConfig = field(default_factory=SemanticConfig)
    scoring: HybridScoringConfig = field(default_factory=HybridScoringConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    # Redis URL for background job queue (empty = use FastAPI BackgroundTasks).
    redis_url: str = ""


def get_verischolar_config() -> VeriScholarConfig:
    """Build the full VeriScholar config from environment variables."""
    return VeriScholarConfig(
        branding=BrandingConfig(
            product_name=os.environ.get("VS_PRODUCT_NAME", BrandingConfig.product_name),
            product_tagline=os.environ.get("VS_PRODUCT_TAGLINE", BrandingConfig.product_tagline),
            product_url=os.environ.get("VS_PRODUCT_URL", BrandingConfig.product_url),
        ),
        scholarly=ScholarlyAPIConfig(
            openalex_email=os.environ.get("VS_OPENALEX_EMAIL", ""),
            openalex_max_per_second=float(
                os.environ.get("VS_OPENALEX_RPS", ScholarlyAPIConfig.openalex_max_per_second)
            ),
            core_api_key=os.environ.get("VS_CORE_API_KEY", ""),
            core_max_per_second=float(
                os.environ.get("VS_CORE_RPS", ScholarlyAPIConfig.core_max_per_second)
            ),
            pubmed_email=os.environ.get("VS_PUBMED_EMAIL", ""),
            pubmed_api_key=os.environ.get("VS_PUBMED_API_KEY", ""),
            crossref_email=os.environ.get("VS_CROSSREF_EMAIL", ""),
        ),
        semantic=SemanticConfig(
            model_name=os.environ.get("VS_SEMANTIC_MODEL", SemanticConfig.model_name),
            embedding_dim=int(
                os.environ.get("VS_EMBEDDING_DIM", SemanticConfig.embedding_dim)
            ),
            device=os.environ.get("VS_DEVICE", SemanticConfig.device),
            faiss_index_type=os.environ.get(
                "VS_FAISS_INDEX_TYPE", SemanticConfig.faiss_index_type
            ),
            semantic_top_k=int(
                os.environ.get("VS_SEMANTIC_TOP_K", SemanticConfig.semantic_top_k)
            ),
            semantic_threshold=float(
                os.environ.get("VS_SEMANTIC_THRESHOLD", SemanticConfig.semantic_threshold)
            ),
            use_cross_encoder=os.environ.get("VS_USE_CROSS_ENCODER", "0") == "1",
            cross_encoder_model=os.environ.get(
                "VS_CROSS_ENCODER_MODEL", SemanticConfig.cross_encoder_model
            ),
        ),
        scoring=HybridScoringConfig(
            exact_match_weight=float(
                os.environ.get("VS_EXACT_WEIGHT", HybridScoringConfig.exact_match_weight)
            ),
            fuzzy_match_weight=float(
                os.environ.get("VS_FUZZY_WEIGHT", HybridScoringConfig.fuzzy_match_weight)
            ),
            semantic_match_weight=float(
                os.environ.get("VS_SEMANTIC_WEIGHT", HybridScoringConfig.semantic_match_weight)
            ),
            enable_fuzzy=os.environ.get("VS_ENABLE_FUZZY", "1") == "1",
            enable_semantic=os.environ.get("VS_ENABLE_SEMANTIC", "1") == "1",
        ),
        security=SecurityConfig(
            jwt_secret=os.environ.get("VS_JWT_SECRET", SecurityConfig.jwt_secret),
            max_upload_bytes=int(
                os.environ.get("VS_MAX_UPLOAD_BYTES", SecurityConfig.max_upload_bytes)
            ),
            rate_limit_rpm=int(
                os.environ.get("VS_RATE_LIMIT_RPM", SecurityConfig.rate_limit_rpm)
            ),
            cors_origins=os.environ.get("VS_CORS_ORIGINS", SecurityConfig.cors_origins),
            auth_enabled=os.environ.get("VS_AUTH_ENABLED", "0") == "1",
        ),
        redis_url=os.environ.get("VS_REDIS_URL", ""),
    )
