import pytest
from relay.library import build_chunk_library, Chunk, get_page_text

def test_chunk_library_structure():
    chunks = build_chunk_library(target_words=300)
    assert len(chunks) > 50, "Library should produce substantial chunks across 3 papers"
    
    papers = {c.paper for c in chunks}
    assert papers == {"attention_v7", "moshi_v1", "dynchunk_v2"}, "All 3 papers must be present"
    
    for c in chunks:
        assert isinstance(c.chunk_id, str)
        assert c.page >= 1
        assert len(c.text) > 0
        assert c.word_count > 0
        assert isinstance(c.section, str)
        assert isinstance(c.version, str)

def test_page_boundaries_preserved():
    chunks = build_chunk_library(target_words=200)
    # Check that each chunk's text is indeed a subset of that page's text
    for c in chunks[:30]:
        page_text = get_page_text(c.paper, c.page)
        assert page_text is not None
        # Normalize whitespace in both and test substring
        norm_page = " ".join(page_text.lower().split())
        norm_chunk = " ".join(c.text.lower().split()[:8])
        assert norm_chunk in norm_page

def test_different_chunk_sizes():
    chunks_200 = build_chunk_library(target_words=200)
    chunks_400 = build_chunk_library(target_words=400)
    # Smaller target words should produce strictly more chunks
    assert len(chunks_200) > len(chunks_400)
