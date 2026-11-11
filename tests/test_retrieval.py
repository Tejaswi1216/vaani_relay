import pytest
import yaml
from relay.retrieval import retrieve_passages, RETRIEVAL_SETTINGS
from relay.config import QUESTIONS_FILE

def load_questions():
    with open(QUESTIONS_FILE, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return {q["id"]: q for q in data}

@pytest.mark.parametrize("qid,expected_paper,expected_page,terms_to_find", [
    ("q01", "attention_v7", 4, ["softmax"]),
    ("q04", "attention_v7", 5, ["linear transformations", "512"]),
    ("q11", "dynchunk_v2", 4, ["encoder", "main network", "decoder"]),
    ("q12", "dynchunk_v2", 6, ["cosine similarity"]),
    ("q16", "dynchunk_v2", 17, ["3.6", "data efficiency"]),
])
def test_retrieval_hits_on_gold_questions(qid, expected_paper, expected_page, terms_to_find):
    questions = load_questions()
    q_data = questions[qid]
    query = q_data["question"]

    # Test retrieval across setting_2 (balanced) and setting_3 (reranked)
    for setting in ["setting_2", "setting_3"]:
        results = retrieve_passages(query, setting_name=setting)
        assert len(results) > 0, f"Retrieval returned empty for {qid} on {setting}"
        
        # Check if gold paper and page are among retrieved candidates
        found_gold = any(c.paper == expected_paper and c.page == expected_page for c, score in results)
        assert found_gold, f"Gold location ({expected_paper}, page {expected_page}) not in top-{len(results)} results for {qid} under {setting}"

        # Check if key factual terms appear in the top retrieved texts
        combined_text = " ".join(c.text.lower() for c, score in results)
        for term in terms_to_find:
            assert term.lower() in combined_text, f"Term '{term}' missing from retrieved text for {qid} under {setting}"
