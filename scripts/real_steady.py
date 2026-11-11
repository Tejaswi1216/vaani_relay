#!/usr/bin/env python3
"""
scripts/real_steady.py: Executes real steady-load run and per-minute limit demonstration on Groq.

Requirements:
- Live under scripts/real_*.py
- Support --dry-run (prints estimated tokens, makes no call)
- Take --max-tokens (refuses to start if ledger allowance is insufficient)
- Reserve >= 20,000 tokens daily
- Append real usage to results/ledger.jsonl
- Write telemetry to results/raw/real_steady.jsonl and results/results.csv
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
from typing import Dict, Any, List, Optional

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
from relay.metrics import MetricsLogger, RequestRecord, AttemptSpan
from relay.client_real import RealGroqClient
from relay.policy import RetryPolicy, AdmissionController
from relay.pipeline import RelayPipeline

DAILY_QUOTA = 200000
DAILY_SAFETY_RESERVE = 20000
USABLE_DAILY_LIMIT = DAILY_QUOTA - DAILY_SAFETY_RESERVE

ESTIMATED_STEADY_TOKENS_PER_REQ = 1800
ESTIMATED_LIMIT_TOKENS_PER_REQ = 2000

def load_questions() -> List[Dict[str, Any]]:
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

class LedgerAwareRealClient(RealGroqClient):
    """Wraps RealGroqClient to automatically record model tokens to results/ledger.jsonl."""
    def __init__(self, current_req_id_holder: List[str], base_url: str = None, client: Optional[httpx.AsyncClient] = None):
        super().__init__(base_url=base_url or "https://api.groq.com/openai/v1", client=client)
        self.holder = current_req_id_holder

    async def chat_completion(self, messages, max_tokens=220, stream=True, policy=None):
        res = await super().chat_completion(messages=messages, max_tokens=max_tokens, stream=stream, policy=policy)
        ans, ft_ms, t_in, t_out, attempts, status, err_body = res
        req_id = self.holder[0] if self.holder else f"real_{uuid.uuid4().hex[:8]}"
        if t_in > 0 or t_out > 0:
            append_to_ledger(req_id, t_in, t_out)
        return res

async def run_real_steady(
    dry_run: bool,
    max_tokens_limit: int,
    steady_count: int = 10,
    burst_count: int = 5
):
    print("=" * 80)
    print("      THE RELAY: REAL STEADY RUN & LIMIT DEMONSTRATION")
    print("=" * 80)
    
    total_estimated = (steady_count * ESTIMATED_STEADY_TOKENS_PER_REQ) + (burst_count * ESTIMATED_LIMIT_TOKENS_PER_REQ)
    today_spent = read_today_ledger_tokens()
    remaining_allowance = USABLE_DAILY_LIMIT - today_spent

    print(f"Plan:")
    print(f"  • Steady Scenario:   {steady_count} requests spaced ~12s (est. {steady_count * ESTIMATED_STEADY_TOKENS_PER_REQ} tokens)")
    print(f"  • Limit Demo Burst:  {burst_count} concurrent requests (est. {burst_count * ESTIMATED_LIMIT_TOKENS_PER_REQ} tokens)")
    print(f"  • Total Estimated:   {total_estimated} tokens")
    print(f"  • Max Tokens Cap:    {max_tokens_limit} tokens")
    print(f"  • Today's Spent:     {today_spent} tokens")
    print(f"  • Usable Allowance:  {remaining_allowance} tokens (preserving >= {DAILY_SAFETY_RESERVE} reserve)")
    print("-" * 80)

    if total_estimated > max_tokens_limit:
        print(f"[ABORT] Estimated tokens ({total_estimated}) exceeds --max-tokens ({max_tokens_limit}).")
        sys.exit(1)

    if total_estimated > remaining_allowance:
        print(f"[ABORT] Estimated tokens ({total_estimated}) exceeds remaining daily allowance ({remaining_allowance}).")
        sys.exit(1)

    if dry_run:
        print("[DRY RUN SUCCESSFUL] Budget verified. 0 real calls made.")
        print(f"Ready to run with: python scripts/real_steady.py --max-tokens {max_tokens_limit}")
        return

    # Check API key
    api_key = get_groq_api_key()
    if not api_key:
        print("[ERROR] GROQ_API_KEY missing from ~/.config/relay/groq.env (R1).")
        sys.exit(1)

    all_questions = load_questions()
    logger = MetricsLogger()
    raw_file = RESULTS_DIR / "raw" / "real_steady.jsonl"
    raw_file.parent.mkdir(parents=True, exist_ok=True)

    print("\nStarting Phase 9.1: Real Steady Run...")
    current_req_id_holder = [""]

    async with httpx.AsyncClient(timeout=60.0) as http_client:
        groq_client = LedgerAwareRealClient(current_req_id_holder=current_req_id_holder, client=http_client)
        # Steady pipeline uses admission controller with real capacity parameters
        admission = AdmissionController(tpm_capacity=8000.0, max_queue_size=20)
        pipeline = RelayPipeline(
            client=groq_client,
            admission_controller=admission,
            metrics_logger=logger,
            source="real"
        )

        # Check already completed steady questions to support clean resume
        completed_qids = set()
        if raw_file.exists():
            with open(raw_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        try:
                            d = json.loads(line)
                            parts = d.get("request_id", "").split("_")
                            if len(parts) >= 3 and parts[0] == "real" and parts[1] == "steady":
                                completed_qids.add(parts[2])
                        except Exception:
                            pass

        # 1. Steady run (spaced out requests to ensure stable steady state)
        steady_qs = all_questions[:steady_count]
        for i, q in enumerate(steady_qs):
            if q["id"] in completed_qids:
                print(f"  [{i+1}/{steady_count}] [RESUME] Skipping {q['id']} (already completed)")
                continue

            req_id = f"real_steady_{q['id']}_{uuid.uuid4().hex[:6]}"
            current_req_id_holder[0] = req_id
            print(f"  [{i+1}/{steady_count}] Steady: {q['id']} - '{q['question'][:50]}...'")
            
            record = await pipeline.answer_question(
                question=q["question"],
                request_id=req_id,
                scenario="real_steady",
                retrieval_setting="setting_3",
                gold_data=q
            )
            print(f"       -> Status: {record.status} | TTFT: {record.first_token_ms}ms | Total: {record.total_ms}ms | Tokens: {record.tokens_in}+{record.tokens_out}")

            if i < steady_count - 1:
                # Sleep 12s to stay well below 8,000 TPM
                await asyncio.sleep(12.0)

        print("\nStarting Phase 9.2: Real Limit Demonstration...")
        print("Firing concurrent burst to observe real Groq 429 rate limit and retry recovery...")
        
        # 2. Limit demo burst: fire requests concurrently
        burst_qs = all_questions[10:10 + burst_count]
        
        async def run_burst_item(idx: int, item: Dict[str, Any]):
            item_req_id = f"real_limit_{item['id']}_{uuid.uuid4().hex[:6]}"
            # We bypass admission delay slightly or use a tight admission controller to hit the real limit
            raw_groq = RealGroqClient(client=http_client)
            
            # Build full pipeline retrieval context
            from relay.retrieval import retrieve_passages
            passages = retrieve_passages(item["question"], setting_name="setting_2")
            ctx = "\n\n---\n\n".join([f"[{c.paper}, page {c.page}, section {c.section}]\n{c.text}" for c, _ in passages])
            prompt = f"Context passages:\n{ctx}\n\nQuestion: {item['question']}\n\nCited Answer:"
            
            t0 = time.perf_counter()
            start_iso = datetime.now(timezone.utc).isoformat()
            
            ans, ft_ms, t_in, t_out, attempts, status, err_body = await raw_groq.chat_completion(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=220,
                policy=RetryPolicy(max_retries=2, base_backoff_ms=2500.0, max_backoff_ms=10000.0)
            )
            total_ms = int((time.perf_counter() - t0) * 1000.0)
            
            if t_in > 0 or t_out > 0:
                append_to_ledger(item_req_id, t_in, t_out)
                
            rec = RequestRecord(
                request_id=item_req_id,
                scenario="real_limit_demo",
                source="real",
                start_time=start_iso,
                first_token_ms=int(ft_ms) if ft_ms is not None else None,
                total_ms=total_ms,
                status=status,
                retries=max(0, len(attempts) - 1),
                tokens_in=t_in,
                tokens_out=t_out,
                retrieval_setting="setting_2"
            )
            rec.attempts = attempts
            rec.raw_response_text = ans
            logger.log_request(rec)
            print(f"  Burst [{idx+1}/{burst_count}] {item['id']} -> Status: {status} | Retries: {rec.retries} | Total: {total_ms}ms")
            return rec

        burst_tasks = [run_burst_item(i, q) for i, q in enumerate(burst_qs)]
        burst_results = await asyncio.gather(*burst_tasks)

    print("\n" + "=" * 80)
    print("Real Steady & Limit Demo Run Completed Successfully.")
    print("=" * 80)

def main():
    parser = argparse.ArgumentParser(description="Execute real steady run and limit demo against Groq API.")
    parser.add_argument("--dry-run", action="store_true", help="Print token estimate and exit without making calls.")
    parser.add_argument("--max-tokens", type=int, default=30000, help="Maximum real tokens allowed to spend.")
    parser.add_argument("--steady-count", type=int, default=10, help="Number of steady requests.")
    parser.add_argument("--burst-count", type=int, default=5, help="Number of concurrent burst requests.")
    args = parser.parse_args()

    asyncio.run(run_real_steady(
        dry_run=args.dry_run,
        max_tokens_limit=args.max_tokens,
        steady_count=args.steady_count,
        burst_count=args.burst_count
    ))

if __name__ == "__main__":
    main()
