import pytest

from noplag_engine.chunking import Chunk, chunk_document


def test_empty_text_returns_empty():
    assert chunk_document("") == []


def test_single_sentence_yields_one_chunk():
    text = "The quick brown fox jumps over the lazy dog."
    chunks = chunk_document(text)
    assert len(chunks) == 1
    assert chunks[0].sentence_count == 1
    assert chunks[0].chunk_index == 0
    assert chunks[0].text == text


def test_fewer_than_sentences_per_chunk_yields_one_chunk():
    text = "First sentence. Second sentence. Third sentence."  # 3 < 4
    chunks = chunk_document(text)
    assert len(chunks) == 1
    assert chunks[0].sentence_count == 3


def test_multi_chunk_with_overlap_correctness():
    # 10 sentences, defaults (4 per chunk, 1 overlap → stride 3) →
    # chunks span [0:4], [3:7], [6:10] → 3 chunks
    sentences = [f"Sentence number {i}." for i in range(10)]
    text = " ".join(sentences)
    chunks = chunk_document(text)
    assert len(chunks) == 3
    assert chunks[0].sentence_count == 4
    assert chunks[1].sentence_count == 4
    assert chunks[2].sentence_count == 4
    # Chunks should be in ascending order
    for i in range(1, len(chunks)):
        assert chunks[i].char_start >= chunks[i - 1].char_start
        assert chunks[i].chunk_index == i


def test_char_offset_invariant():
    text = (
        "First sentence with some words. Second sentence is here. "
        "Third sentence follows. Fourth one. Fifth sentence wraps up. "
        "Sixth and final sentence."
    )
    for chunk in chunk_document(text):
        assert chunk.text == text[chunk.char_start : chunk.char_end], (
            f"chunk {chunk.chunk_index} text != original[start:end]"
        )


def test_adjacent_chunks_share_overlap_sentences():
    # Build a text where each sentence is uniquely identifiable
    sentences = [f"Marker {i} content here." for i in range(8)]
    text = " ".join(sentences)
    chunks = chunk_document(text, sentences_per_chunk=4, overlap=1)
    # With 8 sentences, stride 3 → chunks at [0:4], [3:7], [6:8]
    assert len(chunks) == 3
    # Chunk 1 starts where chunk 0's overlap region begins: sentence 3
    assert "Marker 3" in chunks[0].text
    assert "Marker 3" in chunks[1].text
    # Chunk 1 ends at sentence 6 (inclusive); chunk 2 starts at sentence 6
    assert "Marker 6" in chunks[1].text
    assert "Marker 6" in chunks[2].text
    # Sentences exclusive to chunk 0 (0,1,2) shouldn't appear in chunk 1
    assert "Marker 0" not in chunks[1].text
    assert "Marker 1" not in chunks[1].text


def test_final_chunk_handles_remainder():
    # 8 sentences with defaults → stride 3 → chunks at [0:4], [3:7], [6:8]
    # Last chunk has only 2 sentences (the remainder)
    sentences = [f"Sentence {i}." for i in range(8)]
    text = " ".join(sentences)
    chunks = chunk_document(text)
    assert chunks[-1].sentence_count == 2
    # The final chunk should still satisfy the char-offset invariant
    assert chunks[-1].text == text[chunks[-1].char_start : chunks[-1].char_end]


def test_overlap_equal_to_sentences_per_chunk_raises():
    with pytest.raises(ValueError, match="must be strictly less than"):
        chunk_document("Some text here.", sentences_per_chunk=4, overlap=4)


def test_overlap_greater_than_sentences_per_chunk_raises():
    with pytest.raises(ValueError, match="must be strictly less than"):
        chunk_document("Some text here.", sentences_per_chunk=4, overlap=5)


def test_multilingual_spanish():
    # Spanish text exercising the language parameter
    text = (
        "El gato come pescado fresco todos los días. "
        "María lee un libro interesante en la biblioteca. "
        "El cielo está despejado y azul esta tarde. "
        "Los niños juegan en el parque después de la escuela. "
        "Mañana iremos a la playa con toda la familia."
    )
    chunks = chunk_document(text, language="es")
    assert len(chunks) >= 1
    # Char-offset invariant holds across languages
    for chunk in chunks:
        assert chunk.text == text[chunk.char_start : chunk.char_end]
    # All 5 sentences should be represented across the chunks
    total_sentences = sum(c.sentence_count for c in chunks)
    # With overlap, total_sentences > 5 is expected
    assert total_sentences >= 5


def test_determinism():
    text = (
        "First sentence here. Second one follows. Third comes next. "
        "Fourth in line. Fifth and final."
    )
    first = chunk_document(text)
    repeats = [chunk_document(text) for _ in range(4)]
    assert all(r == first for r in repeats)


def test_chunk_dataclass_is_frozen():
    chunk = Chunk(chunk_index=0, char_start=0, char_end=10, text="hello", sentence_count=1)
    with pytest.raises(Exception):  # noqa: B017
        chunk.chunk_index = 5  # type: ignore[misc]
