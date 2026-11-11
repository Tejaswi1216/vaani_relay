import json
import re

def check_paper(paper_name):
    path = f"data/text/{paper_name}.json"
    with open(path, "r", encoding="utf-8") as f:
        pages = json.load(f)
    print(f"\n==================== {paper_name} ({len(pages)} pages) ====================")
    for p in pages:
        p_num = p["page"]
        text = p["text"]
        # Look for tables (markdown pipes or tabular markers)
        table_lines = [l for l in text.split("\n") if "|" in l]
        # Look for math / equations
        math_matches = re.findall(r"(\$[^\$]+\$|\\\[.+?\\\])", text)
        # Look for headers/footers (e.g. arXiv header)
        arxiv_hdr = [l for l in text.split("\n") if "arXiv:" in l]
        if p_num in [1, 4, 5, 6, 8, 9, 12, 13, 17]:
            print(f"Page {p_num}: {len(text)} chars | {len(table_lines)} table lines | {len(math_matches)} inline math snippets")
            if arxiv_hdr:
                print(f"   Header/Footer detected: {arxiv_hdr[0][:80]}")

def inspect_page_content(paper_name, page_num):
    path = f"data/text/{paper_name}.json"
    with open(path, "r", encoding="utf-8") as f:
        pages = json.load(f)
    p = pages[page_num - 1]
    print(f"\n--- Detailed view: {paper_name} Page {page_num} ---")
    print(p["text"][:1500])
    if len(p["text"]) > 1500:
        print("\n[... intermediate content ...]\n")
        print(p["text"][-800:])

if __name__ == "__main__":
    for name in ["attention_v7", "moshi_v1", "dynchunk_v2"]:
        check_paper(name)
    
    # Specific problem areas:
    # 1. Attention equation on page 4
    inspect_page_content("attention_v7", 4)
    # 2. Attention Table 1/2 on page 9
    inspect_page_content("attention_v7", 9)
    # 3. Moshi training phases on page 8
    inspect_page_content("moshi_v1", 8)
    # 4. DynChunk Table 4 on page 17
    inspect_page_content("dynchunk_v2", 17)
