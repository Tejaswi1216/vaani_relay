import yaml
import json
from relay.retrieval import retrieve_passages, RETRIEVAL_SETTINGS
from relay.config import QUESTIONS_FILE

def evaluate_retrieval():
    with open(QUESTIONS_FILE, "r", encoding="utf-8") as f:
        questions = yaml.safe_load(f)

    print(f"Loaded {len(questions)} questions.\n")
    
    for s_name, setting in RETRIEVAL_SETTINGS.items():
        print(f"\n==================================================")
        print(f"Evaluating {s_name}: {setting.description}")
        print(f"==================================================")
        hit_gold_page = 0
        hit_gold_paper = 0
        must_contain_pass = 0
        evaluated_q_count = 0

        for q in questions:
            qid = q["id"]
            q_text = q["question"]
            answerable = q.get("answerable", True)
            gold_loc = q.get("gold_location")
            must_contain = q.get("must_contain", [])

            if not answerable or not gold_loc:
                continue

            evaluated_q_count += 1
            results = retrieve_passages(q_text, setting_name=s_name)
            
            top_pages = [(c.paper, c.page) for c, _ in results]
            combined_text = " ".join(c.text.lower() for c, _ in results)

            gold_paper = gold_loc["paper"]
            gold_page = gold_loc["page"]

            paper_hit = any(p == gold_paper for p, _ in top_pages)
            page_hit = any(p == gold_paper and pg == gold_page for p, pg in top_pages)
            must_hit = all(m.lower() in combined_text for m in must_contain)

            if paper_hit:
                hit_gold_paper += 1
            if page_hit:
                hit_gold_page += 1
            if must_hit:
                must_contain_pass += 1
            else:
                print(f"[{qid}] Missing must_contain or wrong page under {s_name}:")
                print(f"   Query: {q_text[:70]}...")
                print(f"   Gold: {gold_paper} p{gold_page} | Top-k: {top_pages}")
                missing = [m for m in must_contain if m.lower() not in combined_text]
                print(f"   Missing terms: {missing}")

        print(f"\nSummary for {s_name}:")
        print(f"  Total answerable questions evaluated: {evaluated_q_count}")
        print(f"  Gold Paper Recall: {hit_gold_paper}/{evaluated_q_count} ({hit_gold_paper/evaluated_q_count*100:.1f}%)")
        print(f"  Gold Page Recall:  {hit_gold_page}/{evaluated_q_count} ({hit_gold_page/evaluated_q_count*100:.1f}%)")
        print(f"  Must Contain Pass: {must_contain_pass}/{evaluated_q_count} ({must_contain_pass/evaluated_q_count*100:.1f}%)")

if __name__ == "__main__":
    evaluate_retrieval()
