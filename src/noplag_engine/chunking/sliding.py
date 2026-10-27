"""Sliding-sentence document chunking.

Splits documents into overlapping multi-sentence chunks for L1 retrieval.
Each chunk's text is the exact substring of the original document — char
offsets are preserved so the chunks table can join back to source
documents and L0 alignment can extend matches without re-slicing.

Sentence segmentation uses pysbd (rule-based, multilingual, no model
download required). The chunk window slides over sentences — not raw
characters — so chunk boundaries respect natural document structure even
on noisy input.

Performance note: pysbd's `.segment()` is linear-ish on documents with
natural paragraph structure (~70ms for a 5000-word doc with `\n\n`
breaks) but degrades super-linearly on long continuous text without
paragraph boundaries (~500ms on the same word count concatenated as one
giant paragraph). For typical ingestion this isn't a concern; if the
document-conversion step flattens paragraph breaks for any
reason, consider re-introducing them before chunking.
"""

from __future__ import annotations

from dataclasses import dataclass

import pysbd


@dataclass(frozen=True)
class Chunk:
    chunk_index: int
    char_start: int
    char_end: int
    text: str
    sentence_count: int


def chunk_document(
    text: str,
    sentences_per_chunk: int = 4,
    overlap: int = 1,
    language: str = "en",
) -> list[Chunk]:
    """Split `text` into overlapping sentence windows.

    With the defaults (sentences_per_chunk=4, overlap=1), chunks span
    sentence indices [0:4], [3:7], [6:10], etc. — `sentences_per_chunk`
    sentences with `overlap` sentences shared between adjacent windows.
    The final chunk handles any remainder (smaller `sentence_count` is
    expected; no padding).

    Parameters
    ----------
    text : str
        Raw input document. Not normalized — char offsets refer to the
        original input.
    sentences_per_chunk : int
        Number of sentences per chunk (default 4). Starting value —
        tune against PAN-PC-11.
    overlap : int
        Number of sentences shared with the previous chunk (default 1).
        Must be strictly less than `sentences_per_chunk`.
    language : str
        ISO 639-1 code passed to pysbd. Supports "en", "es", "de", "fr",
        "it", "pt", "ru", "ja", "zh", and several others.

    Returns
    -------
    list[Chunk]
        Empty if `text` is empty or contains no sentences pysbd can
        detect.

    Raises
    ------
    ValueError
        If `overlap >= sentences_per_chunk` (the window cannot advance).
    """
    if overlap >= sentences_per_chunk:
        msg = (
            f"overlap ({overlap}) must be strictly less than "
            f"sentences_per_chunk ({sentences_per_chunk}); otherwise the "
            f"sliding window cannot advance and would loop or duplicate chunks."
        )
        raise ValueError(msg)
    if not text:
        return []

    segmenter = pysbd.Segmenter(language=language, clean=False, char_span=True)
    sentences = segmenter.segment(text)
    if not sentences:
        return []

    stride = sentences_per_chunk - overlap
    chunks: list[Chunk] = []
    start = 0
    chunk_index = 0
    n = len(sentences)
    while start < n:
        end = min(start + sentences_per_chunk, n)
        span = sentences[start:end]
        char_start = span[0].start
        char_end = span[-1].end
        chunks.append(
            Chunk(
                chunk_index=chunk_index,
                char_start=char_start,
                char_end=char_end,
                text=text[char_start:char_end],
                sentence_count=len(span),
            )
        )
        chunk_index += 1
        # Stop once the window has covered the final sentence — emitting
        # a smaller tail chunk would only duplicate sentences already
        # captured by the previous window's overlap region.
        if end >= n:
            break
        start += stride
    return chunks
