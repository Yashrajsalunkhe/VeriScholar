"""Semantic similarity retrieval using Sentence Transformers + FAISS.

L3 of the detection cascade — finds semantically similar passages that
share meaning but use different wording (paraphrase, summarization).

Architecture:
- Sentence Transformers model encodes passages into dense vectors
- FAISS index provides efficient approximate nearest-neighbor search
- Cross-encoder reranker (optional) refines top candidates

Key design decisions:
- Passage-level embeddings (not whole-document) — long papers compressed
  to a single vector lose fine-grained similarity signal
- Cosine similarity — standard for normalized sentence embeddings
- Configurable threshold — semantic similarity ≠ plagiarism; the system
  flags candidates for human review, not automatic judgement
- Lazy model loading — the model is loaded on first use, not at import
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import numpy as np

logger = logging.getLogger("noplag.semantic")

# Global model cache — loaded lazily on first encode() call.
_encoder = None
_cross_encoder = None


@dataclass(frozen=True)
class SemanticCandidate:
    """A semantically similar passage returned by FAISS retrieval."""
    chunk_id: UUID
    document_id: UUID
    similarity: float  # cosine similarity, 0–1
    text_preview: str = ""
    match_type: str = "semantic"


class SemanticIndex:
    """FAISS-backed semantic index for passage-level retrieval.

    The index stores embeddings for corpus chunks alongside their
    chunk_id and document_id for attribution.  Query passages are
    encoded on the fly and searched against the index.
    """

    def __init__(
        self,
        *,
        model_name: str = "all-MiniLM-L6-v2",
        embedding_dim: int = 384,
        device: str = "cpu",
        index_type: str = "flat",
        nlist: int = 100,
        index_path: str | None = None,
    ) -> None:
        self._model_name = model_name
        self._embedding_dim = embedding_dim
        self._device = device
        self._index_type = index_type
        self._nlist = nlist
        self._index_path = index_path or str(
            Path(os.environ.get("CORPUS_STORAGE_DIR", "/tmp/noplag-corpus"))
            / "faiss_index"
        )

        self._index = None
        self._id_map: list[tuple[UUID, UUID]] = []  # [(chunk_id, doc_id), ...]
        self._is_built = False

    def _get_encoder(self):
        """Lazy-load the sentence transformer model."""
        global _encoder
        if _encoder is None:
            try:
                from sentence_transformers import SentenceTransformer
                logger.info(
                    "Loading sentence transformer model: %s (device=%s)",
                    self._model_name, self._device,
                )
                _encoder = SentenceTransformer(
                    self._model_name, device=self._device
                )
            except ImportError:
                raise ImportError(
                    "sentence-transformers is required for semantic retrieval. "
                    "Install with: pip install sentence-transformers"
                )
        return _encoder

    def _get_cross_encoder(self, model_name: str):
        """Lazy-load the cross-encoder reranker."""
        global _cross_encoder
        if _cross_encoder is None:
            try:
                from sentence_transformers import CrossEncoder
                logger.info("Loading cross-encoder: %s", model_name)
                _cross_encoder = CrossEncoder(model_name, device=self._device)
            except ImportError:
                raise ImportError(
                    "sentence-transformers is required for cross-encoder reranking."
                )
        return _cross_encoder

    def encode(self, texts: list[str]) -> np.ndarray:
        """Encode a batch of texts into embedding vectors."""
        model = self._get_encoder()
        embeddings = model.encode(
            texts,
            batch_size=64,
            show_progress_bar=False,
            normalize_embeddings=True,  # For cosine similarity via dot product
        )
        return np.array(embeddings, dtype=np.float32)

    def build_index(
        self,
        embeddings: np.ndarray,
        id_map: list[tuple[UUID, UUID]],
    ) -> None:
        """Build a FAISS index from pre-computed embeddings.

        Parameters
        ----------
        embeddings : np.ndarray
            Shape (N, embedding_dim), normalized float32 vectors.
        id_map : list[tuple[UUID, UUID]]
            Parallel list of (chunk_id, document_id) for each embedding.
        """
        try:
            import faiss
        except ImportError:
            raise ImportError(
                "faiss-cpu is required for semantic retrieval. "
                "Install with: pip install faiss-cpu"
            )

        n, d = embeddings.shape
        assert d == self._embedding_dim, (
            f"Embedding dimension mismatch: {d} != {self._embedding_dim}"
        )

        if self._index_type == "ivf" and n > self._nlist * 39:
            # IVF index for large corpora (faster search, approximate)
            quantizer = faiss.IndexFlatIP(d)
            self._index = faiss.IndexIVFFlat(quantizer, d, self._nlist)
            self._index.train(embeddings)
            self._index.add(embeddings)
            self._index.nprobe = min(10, self._nlist)
        else:
            # Flat index for small-to-medium corpora (exact search)
            self._index = faiss.IndexFlatIP(d)
            self._index.add(embeddings)

        self._id_map = list(id_map)
        self._is_built = True
        logger.info(
            "FAISS index built: %d vectors, type=%s, dim=%d",
            n, self._index_type, d,
        )

    def search(
        self,
        query_embedding: np.ndarray,
        top_k: int = 50,
        threshold: float = 0.65,
    ) -> list[SemanticCandidate]:
        """Search the index for semantically similar passages.

        Parameters
        ----------
        query_embedding : np.ndarray
            Shape (1, embedding_dim) or (embedding_dim,), normalized.
        top_k : int
            Max candidates to return.
        threshold : float
            Minimum cosine similarity (0–1) to include.

        Returns
        -------
        list[SemanticCandidate]
            Candidates sorted by similarity descending.
        """
        if not self._is_built or self._index is None:
            return []

        if query_embedding.ndim == 1:
            query_embedding = query_embedding.reshape(1, -1)

        scores, indices = self._index.search(
            query_embedding.astype(np.float32), top_k
        )

        candidates = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0 or idx >= len(self._id_map):
                continue
            sim = float(score)  # Inner product of normalized vectors = cosine
            if sim < threshold:
                continue
            chunk_id, doc_id = self._id_map[idx]
            candidates.append(
                SemanticCandidate(
                    chunk_id=chunk_id,
                    document_id=doc_id,
                    similarity=sim,
                )
            )

        return candidates

    def rerank(
        self,
        query_text: str,
        candidates: list[tuple[SemanticCandidate, str]],
        *,
        model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2",
        top_k: int = 10,
    ) -> list[SemanticCandidate]:
        """Rerank candidates using a cross-encoder.

        Parameters
        ----------
        query_text : str
            The query passage.
        candidates : list[tuple[SemanticCandidate, str]]
            Each entry is (candidate, candidate_text).
        model_name : str
            Cross-encoder model name.
        top_k : int
            Return top-K after reranking.
        """
        if not candidates:
            return []

        cross_enc = self._get_cross_encoder(model_name)
        pairs = [(query_text, text) for _, text in candidates]
        scores = cross_enc.predict(pairs)

        scored = [
            (cand, float(score))
            for (cand, _text), score in zip(candidates, scores)
        ]
        scored.sort(key=lambda x: -x[1])

        return [
            SemanticCandidate(
                chunk_id=cand.chunk_id,
                document_id=cand.document_id,
                similarity=score,
                text_preview=cand.text_preview,
                match_type="semantic_reranked",
            )
            for cand, score in scored[:top_k]
        ]

    def save(self, path: str | None = None) -> None:
        """Persist the FAISS index to disk."""
        if self._index is None:
            return
        try:
            import faiss
            save_path = path or self._index_path
            Path(save_path).parent.mkdir(parents=True, exist_ok=True)
            faiss.write_index(self._index, f"{save_path}.index")
            # Save ID map alongside
            import json
            with open(f"{save_path}.idmap", "w") as f:
                json.dump(
                    [(str(c), str(d)) for c, d in self._id_map], f
                )
            logger.info("FAISS index saved to %s", save_path)
        except ImportError:
            logger.warning("faiss not available, cannot save index")

    def load(self, path: str | None = None) -> bool:
        """Load a FAISS index from disk. Returns True on success."""
        try:
            import faiss
            import json
            load_path = path or self._index_path
            index_file = f"{load_path}.index"
            idmap_file = f"{load_path}.idmap"
            if not Path(index_file).exists():
                return False
            self._index = faiss.read_index(index_file)
            with open(idmap_file) as f:
                self._id_map = [
                    (UUID(c), UUID(d)) for c, d in json.load(f)
                ]
            self._is_built = True
            logger.info(
                "FAISS index loaded: %d vectors from %s",
                len(self._id_map), load_path,
            )
            return True
        except Exception as exc:
            logger.debug("Failed to load FAISS index: %s", exc)
            return False
