from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple
import re
from rank_bm25 import BM25Okapi
from relay.library import Chunk, build_chunk_library

@dataclass
class RetrievalSetting:
    name: str
    piece_size: int          # target words per chunk
    pieces_fetched: int      # final top-k returned
    rerank: bool             # whether secondary re-ranking / fusion is applied
    description: str

RETRIEVAL_SETTINGS: Dict[str, RetrievalSetting] = {
    "setting_1": RetrievalSetting(
        name="setting_1",
        piece_size=200,
        pieces_fetched=2,
        rerank=False,
        description="Narrow chunks (200 words), top 2, BM25 only"
    ),
    "setting_2": RetrievalSetting(
        name="setting_2",
        piece_size=400,
        pieces_fetched=3,
        rerank=False,
        description="Balanced chunks (400 words), top 3, BM25 only"
    ),
    "setting_3": RetrievalSetting(
        name="setting_3",
        piece_size=300,
        pieces_fetched=3,
        rerank=True,
        description="Medium chunks (300 words), top 6 BM25 candidates re-ranked to top 3 by query term density"
    ),
}

def tokenize(text: str) -> List[str]:
    """Tokenize query and documents for BM25 matching."""
    # Retain technical identifiers like d_k, d_model, h-net, rvq, etc.
    text = text.lower()
    tokens = re.findall(r"[a-z0-9]+(?:[-_][a-z0-9]+)*", text)
    return tokens

class Retriever:
    """Manages BM25 index and retrieval for a specific chunk size."""

    def __init__(self, piece_size: int = 300):
        self.piece_size = piece_size
        self.chunks: List[Chunk] = build_chunk_library(target_words=piece_size)
        self.corpus_tokens = [tokenize(c.text) for c in self.chunks]
        self.bm25 = BM25Okapi(self.corpus_tokens)

    def retrieve(
        self,
        query: str,
        k: int = 3,
        rerank: bool = False,
        target_paper: Optional[str] = None
    ) -> List[Tuple[Chunk, float]]:
        """
        Retrieves top-k chunks for query.
        Optionally filters by target_paper if specified.
        If rerank is True, fetches candidate pool (2k) and re-ranks using query-term density.
        """
        q_tokens = tokenize(query)
        if not q_tokens:
            return []

        scores = self.bm25.get_scores(q_tokens)
        
        # Combine chunks with scores
        scored_candidates: List[Tuple[Chunk, float]] = []
        for idx, score in enumerate(scores):
            chunk = self.chunks[idx]
            if target_paper and chunk.paper != target_paper:
                continue
            scored_candidates.append((chunk, float(score)))

        # Sort by BM25 score descending
        scored_candidates.sort(key=lambda x: x[1], reverse=True)

        if not rerank:
            return scored_candidates[:k]

        # Re-ranking logic: Candidate pool size = max(k * 2, 6)
        pool_size = min(len(scored_candidates), max(k * 2, 6))
        pool = scored_candidates[:pool_size]

        # Calculate exact query bigram/density boost
        q_text = query.lower()
        q_phrases = re.findall(r"[a-z0-9]{3,}(?:\s+[a-z0-9]{3,})+", q_text)

        reranked: List[Tuple[Chunk, float]] = []
        for chunk, bm25_score in pool:
            text_lower = chunk.text.lower()
            density_bonus = 0.0
            
            # Phrase match bonus
            for phrase in q_phrases:
                if phrase in text_lower:
                    density_bonus += 2.5
            
            # Key technical terms co-occurrence bonus
            q_token_set = set(q_tokens)
            matched_terms = [t for t in q_token_set if t in text_lower]
            overlap_ratio = len(matched_terms) / max(len(q_token_set), 1)
            
            combined_score = bm25_score + density_bonus + (overlap_ratio * 3.0)
            reranked.append((chunk, combined_score))

        reranked.sort(key=lambda x: x[1], reverse=True)
        return reranked[:k]

# Cached retrievers per piece_size to avoid rebuilding indices repeatedly
_RETRIEVER_CACHE: Dict[int, Retriever] = {}

def get_retriever(piece_size: int) -> Retriever:
    if piece_size not in _RETRIEVER_CACHE:
        _RETRIEVER_CACHE[piece_size] = Retriever(piece_size=piece_size)
    return _RETRIEVER_CACHE[piece_size]

def retrieve_passages(
    query: str,
    setting_name: str = "setting_2",
    target_paper: Optional[str] = None
) -> List[Tuple[Chunk, float]]:
    """Helper entry-point executing retrieval under a named setting."""
    setting = RETRIEVAL_SETTINGS.get(setting_name, RETRIEVAL_SETTINGS["setting_2"])
    retriever = get_retriever(setting.piece_size)
    return retriever.retrieve(
        query=query,
        k=setting.pieces_fetched,
        rerank=setting.rerank,
        target_paper=target_paper
    )
