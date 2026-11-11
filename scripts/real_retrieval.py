#!/usr/bin/env python3
"""
scripts/real_retrieval.py: Evaluates 3 retrieval settings across all 20 benchmark questions on real Groq service.

Rules:
- R1: Key loaded only inside relay.config.
- R2: Supports --dry-run, --max-tokens, ledger-guarded against results/ledger.jsonl, appends real usage.
- Resumable: Skips already completed (setting, qid) pairs recorded in results/raw/real_retrieval.jsonl.
- Auto-grades answers and writes results to results/raw/grades.jsonl, results/raw/real_retrieval.jsonl, and results/results.csv.
"""

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Set, Tuple

import httpx
import yaml

from relay.config import (
    MODEL_NAME,
    QUESTIONS_FILE,
    RESULTS_DIR,
    RESULTS_CSV,
    LEDGER_FILE,
    get_groq_api_key
)
from relay.library import Chunk
from relay.retrieval import retrieve_passages, RETRIEVAL_SETTINGS
from relay.verifier import verify_answer, extract_citations, extract_quotes
from relay.metrics import MetricsLogger, RequestRecord, AttemptSpan
from relay.client_real import RealGroqClient
from relay.policy import RetryPolicy

DAILY_QUOTA = 200000
DAILY_SAFETY_RESERVE = 20000
USABLE_DAILY_LIMIT = DAILY_QUOTA - DAILY_SAFETY_RESERVE

ESTIMATED_TOKENS_PER_SETTING = {
    "setting_1": 650,   # 200 words x 2 chunks + prompt overhead
    "setting_2": 1000,  # 400 words x 3 chunks + prompt overhead
    "setting_3": 1450   # 300 words x 3 chunks (reranked) + prompt overhead
}

def load_questions():
    with open(QUESTIONS_FILE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def read_today_ledger_tokens() -> int:
    if not LEDGER_FILE.exists():
        return 0
    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    total = 0
    with open(LEDGER_FILE, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                if rec.get("timestamp", "").startswith(today_utc):
                    total += rec.get("total_tokens", 0)
            except Exception:
                continue
    return total

def append_to_ledger(req_id: str, prompt_toks: int, comp_toks: int):
    LEDGER_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": req_id,
        "model": MODEL_NAME,
        "prompt_tokens": prompt_toks,
        "completion_tokens": comp_toks,
        "total_tokens": prompt_toks + comp_toks
    }
    with open(LEDGER_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")

def get_completed_pairs(raw_file: Path) -> Set[Tuple[str, str]]:
    """Returns set of (setting, qid) pairs already successfully executed."""
    completed = set()
    if not raw_file.exists():
        return completed
    with open(raw_file, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                d = json.loads(line)
                if d.get("status") == "ok":
                    completed.add((d.get("retrieval_setting"), d.get("qid")))
            except Exception:
                continue
    return completed

def auto_grade(answer_text: str, gold_data: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """
    Grades answer according to Phase 8 specifications:
    - correct: must_contain facts appear AND cited page matches gold_location
    - refused_correctly: unanswerable question correctly refused
    - refused_wrongly: answerable question refused
    - wrong: factual or citation mismatch
    """
    answerable = gold_data.get("answerable", True)
    must_contain = gold_data.get("must_contain", [])
    gold_loc = gold_data.get("gold_location")

    ans_lower = answer_text.lower() if answer_text else ""
    refusal_keywords = ["cannot", "not contain", "does not mention", "do not contain", "not addressed", "library does not"]
    is_refusal = any(kw in ans_lower for kw in refusal_keywords)

    citations = extract_citations(answer_text or "")
    cited_pages = [(c.paper, c.page) for c in citations]
    found_terms = [m for m in must_contain if m.lower() in ans_lower]

    details = {
        "is_refusal": is_refusal,
        "cited_pages": cited_pages,
        "found_terms": found_terms,
        "missing_terms": [m for m in must_contain if m.lower() not in ans_lower]
    }

    if not answerable:
        grade = "refused_correctly" if is_refusal else "wrong"
        return grade, details

    if is_refusal:
        return "refused_wrongly", details

    # Answerable: check gold location match and must_contain terms
    loc_match = False
    if gold_loc:
        expected_paper = gold_loc.get("paper")
        expected_page = gold_loc.get("page")
        loc_match = any(p == expected_paper and pg == expected_page for p, pg in cited_pages)

    all_terms_present = (len(found_terms) == len(must_contain))

    if loc_match and all_terms_present:
        return "correct", details
    else:
        return "wrong", details

async def run_retrieval_experiment(dry_run: bool, max_tokens_limit: int):
    questions = load_questions()
    settings = ["setting_1", "setting_2", "setting_3"]
    total_calls = len(questions) * len(settings) # 20 x 3 = 60

    est_tokens_total = sum(ESTIMATED_TOKENS_PER_SETTING[s] * len(questions) for s in settings) # ~62,000

    print("=" * 80)
    print("         THE RELAY: REAL RETRIEVAL COMPARISON EXPERIMENT")
    print("=" * 80)
    print(f"Total Settings: {settings}")
    print(f"Total Questions: {len(questions)}")
    print(f"Planned Calls: {total_calls}")
    print(f"Estimated Real Token Cost: {est_tokens_total} tokens")

    today_spent = read_today_ledger_tokens()
    remaining_allowance = USABLE_DAILY_LIMIT - today_spent
    print(f"Tokens Spent Today:        {today_spent} / {DAILY_QUOTA} (Reserve: {DAILY_SAFETY_RESERVE})")
    print(f"Usable Allowance:          {remaining_allowance} tokens")
    print(f"Max Tokens Allowed:        {max_tokens_limit}")

    if est_tokens_total > max_tokens_limit:
        print(f"\n[ABORT] Estimated cost ({est_tokens_total}) exceeds --max-tokens ({max_tokens_limit}).")
        sys.exit(1)

    if est_tokens_total > remaining_allowance:
        print(f"\n[ABORT] Insufficient daily allowance ({remaining_allowance}) to safely run experiment.")
        sys.exit(1)

    if dry_run:
        print("\n[DRY RUN SUCCESSFUL] Budget verified. 0 real calls made.")
        print(f"Safe to run with: python scripts/real_retrieval.py --max-tokens {max_tokens_limit}")
        return

    # Real run execution
    api_key = get_groq_api_key()
    if not api_key:
        print("\n[ERROR] GROQ_API_KEY missing from ~/.config/relay/groq.env (R1).")
        sys.exit(1)

    raw_retrieval_file = RESULTS_DIR / "raw" / "real_retrieval.jsonl"
    grades_file = RESULTS_DIR / "raw" / "grades.jsonl"
    completed = get_completed_pairs(raw_retrieval_file)
    print(f"Resumability: Found {len(completed)} already completed (setting, qid) pairs. Skipping them.")

    logger = MetricsLogger()
    print("\nStarting Real Retrieval Execution...")

    async with httpx.AsyncClient(timeout=60.0) as http_client:
        groq_client = RealGroqClient(client=http_client)

        for setting_name in settings:
            print(f"\n--- RUNNING SETTING: {setting_name} ({RETRIEVAL_SETTINGS[setting_name].description}) ---")
            
            for q_idx, q_item in enumerate(questions):
                qid = q_item["id"]
                if (setting_name, qid) in completed:
                    print(f"  [SKIPPED] {setting_name} - {qid} (already recorded)")
                    continue

                question_text = q_item["question"]
                req_id = f"real_ret_{setting_name}_{qid}_{uuid.uuid4().hex[:6]}"
                start_mono = time.perf_counter()
                start_iso = datetime.now(timezone.utc).isoformat()

                # Step 1: Interpret Question (detect target paper)
                target_paper = None
                q_lower = question_text.lower()
                if "transformer" in q_lower and "h-net" not in q_lower and "moshi" not in q_lower:
                    target_paper = "attention_v7"
                elif ("moshi" in q_lower or "helium" in q_lower) and "h-net" not in q_lower:
                    target_paper = "moshi_v1"
                elif ("h-net" in q_lower or "dynamic chunking" in q_lower) and "moshi" not in q_lower and "transformer" not in q_lower:
                    target_paper = "dynchunk_v2"

                # Step 2: Retrieve Passages
                passages = retrieve_passages(question_text, setting_name=setting_name, target_paper=target_paper)

                # Context formatting
                context_blocks = [f"[{c.paper}, page {c.page}, section {c.section}]\n{c.text}" for c, _ in passages]
                context_str = "\n\n---\n\n".join(context_blocks)

                system_prompt = (
                    "You are a factual research assistant answering questions about arXiv papers. "
                    "You must strictly ground your answer ONLY in the provided passages. "
                    "Cite every claim with exact citations in the format [paper_id, page N] and quote exact words in quotation marks. "
                    "If the passages do not state the answer, state plainly that you cannot answer."
                )
                user_prompt = f"Context passages:\n{context_str}\n\nQuestion: {question_text}\n\nCited Answer:"

                # Step 4: Model call (buffered internally per R7)
                ans, ft_ms, t_in, t_out, attempts, status, err_body = await groq_client.chat_completion(
                    messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
                    max_tokens=220,
                    policy=RetryPolicy(max_retries=2)
                )
                total_ms = int((time.perf_counter() - start_mono) * 1000.0)

                # Auto-grade
                grade, grade_details = auto_grade(ans or "", q_item)

                # Step 5: Verification check
                ver_res = verify_answer(ans or "")
                if not ver_res.valid and status == "ok":
                    # If verification failed, log details
                    grade_details["verification_errors"] = ver_res.errors

                # Log results.csv and raw JSONL
                rec = RequestRecord(
                    request_id=req_id,
                    scenario="real_retrieval",
                    source="real",
                    start_time=start_iso,
                    first_token_ms=total_ms if status == "ok" else None,
                    total_ms=total_ms,
                    status=status,
                    retries=max(0, len(attempts) - 1),
                    tokens_in=t_in,
                    tokens_out=t_out,
                    retrieval_setting=setting_name,
                    answer_grade=grade
                )
                rec.attempts = attempts
                rec.raw_response_text = ans
                logger.log_request(rec)

                if t_in > 0 or t_out > 0:
                    append_to_ledger(req_id, t_in, t_out)

                raw_ret_entry = {
                    "request_id": req_id,
                    "qid": qid,
                    "retrieval_setting": setting_name,
                    "status": status,
                    "first_token_ms": total_ms if status == "ok" else None,
                    "total_ms": total_ms,
                    "tokens_in": t_in,
                    "tokens_out": t_out,
                    "grade": grade,
                    "grade_details": grade_details,
                    "answer_text": ans,
                    "attempts": [att.__dict__ for att in attempts]
                }
                with open(raw_retrieval_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(raw_ret_entry) + "\n")

                grade_entry = {
                    "request_id": req_id,
                    "qid": qid,
                    "retrieval_setting": setting_name,
                    "grade": grade,
                    "grade_method": "auto_fact_and_citation_match",
                    "details": grade_details
                }
                with open(grades_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(grade_entry) + "\n")

                print(f"  [{setting_name} - {qid}] grade={grade:<17} | TTFT={rec.first_token_ms or 'N/A'}ms | tokens={t_in}+{t_out} | status={status}")

                # Rate pacing: Sleep 7.5s between calls to stay comfortably under 8K TPM and 30 RPM
                await asyncio.sleep(7.5)

    print("\n" + "=" * 80)
    print("REAL RETRIEVAL EXPERIMENT COMPLETED")
    print(f"Raw retrieval records: {raw_retrieval_file}")
    print(f"Grade records:         {grades_file}")
    print(f"Results CSV:           {RESULTS_CSV}")
    print(f"Ledger:                {LEDGER_FILE}")
    print("=" * 80)

def main():
    parser = argparse.ArgumentParser(description="Real Retrieval Comparison across settings")
    parser.add_argument("--dry-run", action="store_true", help="Perform budget check without network calls")
    parser.add_argument("--max-tokens", type=int, default=75000, help="Max tokens allowed for experiment")
    args = parser.parse_args()

    asyncio.run(run_retrieval_experiment(dry_run=args.dry_run, max_tokens_limit=args.max_tokens))

if __name__ == "__main__":
    main()
