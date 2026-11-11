from dataclasses import dataclass, asdict
from typing import List, Dict, Optional
import json
import re
from pathlib import Path
from relay.config import TEXT_DIR, PAPERS_INFO

@dataclass
class Chunk:
    chunk_id: str
    paper: str
    version: str
    page: int
    section: str
    text: str
    word_count: int

    def to_dict(self) -> dict:
        return asdict(self)

def detect_section_heading(line: str) -> Optional[str]:
    """Detects section headings from markdown text."""
    clean = line.strip()
    # Markdown headings (# Heading, ## Heading, ### Heading)
    m = re.match(r"^#{1,4}\s+\**([0-9A-Za-z\s\.\-:,_]+?)\**$", clean)
    if m:
        return m.group(1).strip()
    # Bold numbered sections, e.g., **3.2 Attention**
    m = re.match(r"^\*\*([0-9]+(?:\.[0-9]+)*\s+[A-Za-z\s\.\-:,_]+?)\*\*$", clean)
    if m:
        return m.group(1).strip()
    # Numbered sections without markdown formatting, e.g., "1 Introduction"
    m = re.match(r"^([0-9]+(?:\.[0-9]+)*\s+[A-Z][A-Za-z\s\.\-:,_]{2,40})$", clean)
    if m:
        return m.group(1).strip()
    return None

def split_into_paragraphs(text: str) -> List[str]:
    """Splits text into meaningful paragraphs while preserving markdown structure."""
    raw_paras = re.split(r"\n\s*\n", text)
    paras = [p.strip() for p in raw_paras if p.strip()]
    return paras

def chunk_page_text(
    paper: str,
    version: str,
    page: int,
    text: str,
    target_words: int = 300,
    current_section: str = "General"
) -> tuple[List[Chunk], str]:
    """
    Chunks a single page into chunks of approximately target_words.
    Ensures chunks do NOT cross page boundaries.
    Tracks active section headers across chunks.
    """
    paras = split_into_paragraphs(text)
    chunks: List[Chunk] = []
    current_para_acc: List[str] = []
    current_words = 0
    chunk_idx = 1
    active_section = current_section

    for para in paras:
        # Check if this paragraph or its first line defines a new section
        lines = para.split("\n")
        first_line = lines[0].strip()
        detected_sec = detect_section_heading(first_line)
        if detected_sec:
            active_section = detected_sec

        para_words = len(para.split())
        
        # If adding this paragraph exceeds target_words significantly and we already have content, flush
        if current_words > 0 and (current_words + para_words > target_words * 1.3):
            chunk_text = "\n\n".join(current_para_acc)
            cid = f"{paper}_p{page}_c{chunk_idx}"
            chunks.append(Chunk(
                chunk_id=cid,
                paper=paper,
                version=version,
                page=page,
                section=active_section,
                text=chunk_text,
                word_count=len(chunk_text.split())
            ))
            chunk_idx += 1
            current_para_acc = [para]
            current_words = para_words
        else:
            current_para_acc.append(para)
            current_words += para_words

    # Flush any remaining paragraphs
    if current_para_acc:
        chunk_text = "\n\n".join(current_para_acc)
        cid = f"{paper}_p{page}_c{chunk_idx}"
        chunks.append(Chunk(
            chunk_id=cid,
            paper=paper,
            version=version,
            page=page,
            section=active_section,
            text=chunk_text,
            word_count=len(chunk_text.split())
        ))

    return chunks, active_section

def build_chunk_library(target_words: int = 300) -> List[Chunk]:
    """Loads all extracted paper pages and returns a flat list of page-aware chunks."""
    all_chunks: List[Chunk] = []

    for paper_id, info in PAPERS_INFO.items():
        json_path = TEXT_DIR / f"{paper_id}.json"
        if not json_path.exists():
            continue
        with open(json_path, "r", encoding="utf-8") as f:
            pages = json.load(f)
        
        current_sec = "Introduction"
        for p in pages:
            page_num = p["page"]
            text = p.get("text", "")
            page_chunks, current_sec = chunk_page_text(
                paper=paper_id,
                version=info["version"],
                page=page_num,
                text=text,
                target_words=target_words,
                current_section=current_sec
            )
            all_chunks.extend(page_chunks)

    return all_chunks

def get_page_text(paper: str, page: int) -> Optional[str]:
    """Retrieves full extracted text for a specific paper and page."""
    json_path = TEXT_DIR / f"{paper}.json"
    if not json_path.exists():
        return None
    with open(json_path, "r", encoding="utf-8") as f:
        pages = json.load(f)
    for p in pages:
        if p["page"] == page:
            return p.get("text", "")
    return None
