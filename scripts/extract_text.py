import os
import json
import pymupdf4llm

PAPERS = {
    "attention_v7": "data/papers/attention_v7.pdf",
    "moshi_v1": "data/papers/moshi_v1.pdf",
    "dynchunk_v2": "data/papers/dynchunk_v2.pdf",
}

def extract_paper(name, pdf_path):
    print(f"Extracting {name} from {pdf_path}...")
    pages = pymupdf4llm.to_markdown(pdf_path, page_chunks=True)
    out_pages = []
    for idx, p in enumerate(pages):
        page_num = idx + 1
        text = p.get("text", "")
        metadata = p.get("metadata", {})
        out_pages.append({
            "paper": name,
            "page": page_num,
            "text": text,
            "metadata": metadata
        })
    out_file = f"data/text/{name}.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(out_pages, f, indent=2, ensure_ascii=False)
    print(f"Saved {len(out_pages)} pages to {out_file}")

def main():
    os.makedirs("data/text", exist_ok=True)
    for name, path in PAPERS.items():
        extract_paper(name, path)

if __name__ == "__main__":
    main()
