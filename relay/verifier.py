from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
import re
from relay.library import get_page_text
from relay.config import PAPERS_INFO

@dataclass
class Citation:
    paper: str
    page: int
    section: Optional[str] = None
    raw_citation: str = ""

@dataclass
class VerificationResult:
    valid: bool
    citations: List[Citation] = field(default_factory=list)
    verified_quotes: List[str] = field(default_factory=list)
    missing_quotes: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

def normalize_text_for_matching(text: str) -> str:
    """Normalizes text by removing markdown formatting, lowercasing, and collapsing whitespace."""
    # Strip markdown emphasis and html formatting
    t = re.sub(r"<[^>]+>", " ", text)
    t = re.sub(r"[\*_~`#|]+", " ", t)
    # Replace line breaks and multiple spaces with a single space
    t = re.sub(r"\s+", " ", t)
    return t.strip().lower()

def extract_citations(text: str) -> List[Citation]:
    """Extracts [paper, page N] or [paper, page N, section S] citations."""
    pattern = r"\[([a-zA-Z0-9_]+),\s*(?:page|p\.)\s*(\d+)(?:,\s*(?:section|sec\.)\s*([^\]]+))?\]"
    matches = re.finditer(pattern, text, re.IGNORECASE)
    citations = []
    for m in matches:
        paper_id = m.group(1).lower()
        page_num = int(m.group(2))
        section = m.group(3).strip() if m.group(3) else None
        citations.append(Citation(
            paper=paper_id,
            page=page_num,
            section=section,
            raw_citation=m.group(0)
        ))
    return citations

def extract_quotes(text: str) -> List[str]:
    """Extracts quoted spans from text (min length 4 characters)."""
    # Matches "quote" or “quote”
    quotes = re.findall(r'["“]([^"”\n]{4,})["”]', text)
    return [q.strip() for q in quotes if q.strip()]

def verify_answer(answer: str, require_quote: bool = False) -> VerificationResult:
    """
    Deterministically verifies an answer against source paper pages.
    Checks:
    1. At least one citation exists (unless the answer is an explicit refusal).
    2. Cited paper exists in library.
    3. Cited page exists for that paper.
    4. If quoted spans are present, verifies each quote exists on the cited page(s).
    """
    errors: List[str] = []
    verified_quotes: List[str] = []
    missing_quotes: List[str] = []

    # Check for refusal answers
    refusal_keywords = [
        "cannot answer",
        "does not mention",
        "does not contain",
        "do not contain",
        "not addressed in the paper",
        "not found in",
        "not answered by the papers",
        "the library does not",
        "papers in the library do not",
        "cannot be answered from"
    ]
    is_refusal = any(kw in answer.lower() for kw in refusal_keywords)
    if is_refusal:
        return VerificationResult(valid=True, errors=[])

    citations = extract_citations(answer)
    if not citations:
        errors.append("No valid citations found in answer (expected format [paper, page N]).")
        return VerificationResult(valid=False, errors=errors)

    # Validate paper keys and load page texts
    page_texts: Dict[Tuple[str, int], str] = {}
    for c in citations:
        if c.paper not in PAPERS_INFO:
            errors.append(f"Invalid paper cited: '{c.paper}'. Expected one of {list(PAPERS_INFO.keys())}.")
            continue
        p_text = get_page_text(c.paper, c.page)
        if p_text is None:
            errors.append(f"Cited page {c.page} does not exist in paper '{c.paper}'.")
        else:
            page_texts[(c.paper, c.page)] = normalize_text_for_matching(p_text)

    if errors:
        return VerificationResult(valid=False, citations=citations, errors=errors)

    quotes = extract_quotes(answer)
    if require_quote and not quotes:
        errors.append("Answer contains no quoted source spans.")
        return VerificationResult(valid=False, citations=citations, errors=errors)

    # Check each quote against all cited pages in this answer
    combined_cited_pages = " ".join(page_texts.values())
    for q in quotes:
        norm_q = normalize_text_for_matching(q)
        if norm_q in combined_cited_pages:
            verified_quotes.append(q)
        else:
            # Also check without punctuation in case of typography difference
            clean_q = re.sub(r"[^\w\s]", "", norm_q)
            clean_corpus = re.sub(r"[^\w\s]", "", combined_cited_pages)
            if clean_q and clean_q in clean_corpus:
                verified_quotes.append(q)
            else:
                missing_quotes.append(q)
                errors.append(f"Quoted span not found on cited page(s): \"{q}\"")

    is_valid = len(errors) == 0
    return VerificationResult(
        valid=is_valid,
        citations=citations,
        verified_quotes=verified_quotes,
        missing_quotes=missing_quotes,
        errors=errors
    )
