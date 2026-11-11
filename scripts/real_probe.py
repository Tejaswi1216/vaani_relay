#!/usr/bin/env python3
"""
scripts/real_probe.py: Controlled real-token probe script for Groq API (qwen/qwen3.8-27b).

Rules:
- R1: Loads key only via relay.config.
- R2: Supports --dry-run, takes --max-tokens, checks results/ledger.jsonl, appends real usage.
      The human runs this script.
- Records raw telemetry to results/raw/real_probe.jsonl and results/results.csv (source=real).
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
from typing import Dict, Any, List, Optional, Tuple

import httpx

from relay.config import (
    MODEL_NAME,
    PATIENCE_TTFT_MS,
    PATIENCE_TOTAL_MS,
    RESULTS_DIR,
    RESULTS_CSV,
    LEDGER_FILE,
    get_groq_api_key
)
from relay.metrics import MetricsLogger, RequestRecord, AttemptSpan
from relay.client_real import RealGroqClient
from relay.policy import RetryPolicy

# Token estimation parameters per probe stage
STAGE_ESTIMATES = {
    "stage1_latency": {
        "description": "Latency distribution over 3 prompt sizes (small ~20, med ~250, lg ~750 tokens) x 3 repeats @ conc=1",
        "calls": 9,
        "est_tokens_in": 3 * 20 + 3 * 250 + 3 * 750,      # 60 + 750 + 2250 = 3060
        "est_tokens_out": 3 * 10 + 3 * 40 + 3 * 40,        # 30 + 120 + 120 = 270
        "total": 3330
    },
    "stage2_concurrency": {
        "description": "In-flight concurrency limits at conc=2, 4, 8 with tiny prompts",
        "calls": 14,                                       # 2 + 4 + 8
        "est_tokens_in": 14 * 15,                          # 210
        "est_tokens_out": 14 * 5,                          # 70
        "total": 280
    },
    "stage3_rpm_429": {
        "description": "Deliberate RPM 429 burst: 32 rapid tiny calls in 1 second",
        "calls": 32,
        "est_tokens_in": 32 * 12,                          # 384
        "est_tokens_out": 32 * 5,                          # 160
        "total": 544
    },
    "stage4_tpm_429": {
        "description": "Deliberate TPM 429: 2 large concurrent prompts (2,800 tokens each) sent simultaneously",
        "calls": 2,
        "est_tokens_in": 2 * 2800,                         # 5600
        "est_tokens_out": 2 * 50,                          # 100
        "total": 5700
    },
    "stage5_recovery": {
        "description": "Time until recovery after 429: polling with tiny prompts (up to 8 calls)",
        "calls": 8,
        "est_tokens_in": 8 * 15,                           # 120
        "est_tokens_out": 8 * 5,                           # 40
        "total": 160
    }
}

TOTAL_ESTIMATED_TOKENS = sum(s["total"] for s in STAGE_ESTIMATES.values()) # ~10,014 tokens
DAILY_QUOTA = 200000
DAILY_SAFETY_RESERVE = 20000
USABLE_DAILY_LIMIT = DAILY_QUOTA - DAILY_SAFETY_RESERVE # 180,000

def read_today_ledger_tokens() -> int:
    """Calculates total tokens recorded in ledger for today (UTC)."""
    if not LEDGER_FILE.exists():
        return 0
    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    total_tokens = 0
    with open(LEDGER_FILE, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                ts = rec.get("timestamp", "")
                if ts.startswith(today_utc):
                    total_tokens += rec.get("total_tokens", 0)
            except Exception:
                continue
    return total_tokens

def append_to_ledger(req_id: str, prompt_toks: int, comp_toks: int):
    """Appends model call usage to ledger.jsonl per R2."""
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

async def run_probe(dry_run: bool, max_tokens_limit: int):
    print("=" * 80)
    print("               THE RELAY: REAL GROQ API PROBE SCRIPT")
    print("=" * 80)
    print(f"Model: {MODEL_NAME}")
    print(f"Dry Run: {dry_run}")
    print(f"Max Tokens Allowed: {max_tokens_limit}")
    print("-" * 80)
    print("Probe Stages & Token Breakdown:")
    for name, stg in STAGE_ESTIMATES.items():
        print(f"  • {name:<20}: {stg['calls']:>2} calls | est. {stg['total']:>5} tokens ({stg['description']})")
    print("-" * 80)
    print(f"Total Estimated Token Cost: {TOTAL_ESTIMATED_TOKENS} tokens")

    # Ledger allowance guard (R2)
    today_spent = read_today_ledger_tokens()
    remaining_allowance = USABLE_DAILY_LIMIT - today_spent
    print(f"Today's Spent Tokens (Ledger): {today_spent} / {DAILY_QUOTA} (Reserve: {DAILY_SAFETY_RESERVE})")
    print(f"Current Usable Allowance:     {remaining_allowance} tokens")

    if TOTAL_ESTIMATED_TOKENS > max_tokens_limit:
        print(f"\n[ABORT] Estimated cost ({TOTAL_ESTIMATED_TOKENS}) exceeds --max-tokens ({max_tokens_limit}).")
        sys.exit(1)

    if TOTAL_ESTIMATED_TOKENS > remaining_allowance:
        print(f"\n[ABORT] Insufficient daily allowance ({remaining_allowance}) to safely run probe with {DAILY_SAFETY_RESERVE} reserve.")
        sys.exit(1)

    if dry_run:
        print("\n[DRY RUN SUCCESSFUL] Budget verified. 0 real calls made.")
        print(f"Safe to run with: python scripts/real_probe.py --max-tokens {max_tokens_limit}")
        return

    # Real run: Human confirmed
    api_key = get_groq_api_key()
    if not api_key:
        print("\n[ERROR] GROQ_API_KEY missing from ~/.config/relay/groq.env (R1).")
        sys.exit(1)

    print("\nStarting Real Probe Execution...")
    logger = MetricsLogger()
    raw_probe_file = RESULTS_DIR / "raw" / "real_probe.jsonl"
    
    # Text samples for prompts
    prompt_small = "Say 'Relay Ready' in two words."
    prompt_medium = (
        "Passage from Attention Is All You Need:\n"
        "The Transformer is the first transduction model relying entirely on self-attention to compute representations "
        "of its input and output without using sequence-aligned RNNs or convolution.\n\n"
        "Question: Summarize what makes the Transformer unique in one sentence."
    )
    prompt_large = (
        "Passage from Dynamic Chunking for End-to-End Hierarchical Sequence Modeling:\n"
        "H-Net consists of encoder networks, a main network, and decoder networks. The encoders compress raw input "
        "sequences into shorter chunk representations using dynamic chunking. Unlike heuristic boundaries, H-Net "
        "learns chunk boundaries end-to-end using a routing module that evaluates cosine similarities between encoder outputs.\n"
        "The main network then processes these shorter sequences with high model capacity. Finally, the decoder networks "
        "decompress the representations back to original length while smoothing boundary transitions.\n"
        "Furthermore, by focusing model capacity in the main network, H-Net achieves superior computational efficiency.\n\n"
        "Question: Explain in detail the three core components of H-Net and how sequence compression works."
    )

    async with httpx.AsyncClient(timeout=60.0) as http_client:
        groq_client = RealGroqClient(client=http_client)

        async def execute_probe_call(label: str, prompt: str, max_out: int = 50) -> Dict[str, Any]:
            req_id = f"probe_{label}_{uuid.uuid4().hex[:8]}"
            t0 = time.perf_counter()
            start_iso = datetime.now(timezone.utc).isoformat()
            
            ans, ft_ms, t_in, t_out, attempts, status, err_body = await groq_client.chat_completion(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_out,
                policy=RetryPolicy(max_retries=1)
            )
            total_ms = int((time.perf_counter() - t0) * 1000.0)

            rec = RequestRecord(
                request_id=req_id,
                scenario="real_probe",
                source="real",
                start_time=start_iso,
                first_token_ms=int(ft_ms) if ft_ms is not None else None,
                total_ms=total_ms,
                status=status,
                retries=max(0, len(attempts) - 1),
                tokens_in=t_in,
                tokens_out=t_out,
                retrieval_setting="none",
                answer_grade=""
            )
            rec.attempts = attempts
            rec.raw_response_text = ans
            logger.log_request(rec)

            if t_in > 0 or t_out > 0:
                append_to_ledger(req_id, t_in, t_out)

            probe_log_entry = {
                "request_id": req_id,
                "label": label,
                "timestamp": start_iso,
                "status": status,
                "first_token_ms": ft_ms,
                "total_ms": total_ms,
                "tokens_in": t_in,
                "tokens_out": t_out,
                "attempts": [att.__dict__ for att in attempts],
                "error_body": err_body
            }
            with open(raw_probe_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(probe_log_entry) + "\n")

            print(f"  [{label}] {req_id} -> status: {status} | TTFT: {ft_ms or 'N/A'}ms | total: {total_ms}ms | tokens: in={t_in}, out={t_out}")
            return probe_log_entry

        # --- Stage 1: Latency distribution at concurrency 1 ---
        print("\n--- STAGE 1: Latency Distribution (3 sizes x 3 repeats @ conc=1) ---")
        for i in range(3):
            await execute_probe_call(f"s1_small_r{i+1}", prompt_small, max_out=15)
            await asyncio.sleep(1.0)
            await execute_probe_call(f"s1_med_r{i+1}", prompt_medium, max_out=50)
            await asyncio.sleep(1.0)
            await execute_probe_call(f"s1_large_r{i+1}", prompt_large, max_out=50)
            await asyncio.sleep(2.0)

        # --- Stage 2: Concurrency 2, 4, 8 ---
        print("\n--- STAGE 2: In-Flight Concurrency (2, 4, 8) ---")
        for conc in [2, 4, 8]:
            print(f"  Sending burst of {conc} concurrent requests...")
            tasks = [
                execute_probe_call(f"s2_conc{conc}_{idx+1}", prompt_small, max_out=10)
                for idx in range(conc)
            ]
            await asyncio.gather(*tasks)
            await asyncio.sleep(3.0)

        # --- Stage 3: Deliberate RPM 429 ---
        print("\n--- STAGE 3: Deliberate RPM 429 Probe (32 rapid requests) ---")
        rpm_tasks = [
            execute_probe_call(f"s3_rpm_burst_{idx+1}", "Say 'ok'.", max_out=5)
            for idx in range(32)
        ]
        rpm_results = await asyncio.gather(*rpm_tasks)
        hit_429 = [r for r in rpm_results if r["status"] == "rate_limited"]
        print(f"  RPM 429 Triggered: {len(hit_429)} / 32 requests rate-limited.")
        if hit_429:
            sample_att = hit_429[0]["attempts"][0]
            print(f"  Sample RPM 429 Headers: {sample_att.get('headers')}")
            print(f"  Sample RPM 429 Body:    {hit_429[0].get('error_body')}")

        # --- Stage 4: Deliberate TPM 429 ---
        print("\n--- STAGE 4: Deliberate TPM 429 Probe (2 massive prompts in parallel) ---")
        huge_prompt = (prompt_large + "\n") * 12 # ~2,800 tokens each
        tpm_tasks = [
            execute_probe_call(f"s4_tpm_huge_1", huge_prompt, max_out=100),
            execute_probe_call(f"s4_tpm_huge_2", huge_prompt, max_out=100)
        ]
        tpm_results = await asyncio.gather(*tpm_tasks)
        tpm_429 = [r for r in tpm_results if r["status"] == "rate_limited"]
        print(f"  TPM 429 Triggered: {len(tpm_429)} / 2 requests rate-limited.")
        if tpm_429:
            sample_att = tpm_429[0]["attempts"][0]
            print(f"  Sample TPM 429 Headers: {sample_att.get('headers')}")
            print(f"  Sample TPM 429 Body:    {tpm_429[0].get('error_body')}")

        # --- Stage 5: Time Until Recovery ---
        print("\n--- STAGE 5: Recovery Measurement ---")
        recovery_start = time.perf_counter()
        recovered = False
        for poll_idx in range(12):
            await asyncio.sleep(2.0)
            res = await execute_probe_call(f"s5_recover_poll_{poll_idx+1}", "Say 'ping'.", max_out=5)
            if res["status"] == "ok":
                recovery_duration = time.perf_counter() - recovery_start
                print(f"  [RECOVERED] System resumed 200 OK after {recovery_duration:.2f} seconds.")
                recovered = True
                break

        if not recovered:
            print("  [WARN] System did not recover within 24 seconds of polling.")

    print("\n" + "=" * 80)
    print("PROBE EXECUTION COMPLETED")
    print(f"Raw data appended to: {raw_probe_file}")
    print(f"Results appended to:  {RESULTS_CSV}")
    print(f"Ledger appended to:   {LEDGER_FILE}")
    print("=" * 80)

def main():
    parser = argparse.ArgumentParser(description="Real Groq API Probe Script for The Relay")
    parser.add_argument("--dry-run", action="store_true", help="Estimate tokens without making network calls")
    parser.add_argument("--max-tokens", type=int, default=25000, help="Max tokens allowed for probe run")
    args = parser.parse_args()

    asyncio.run(run_probe(dry_run=args.dry_run, max_tokens_limit=args.max_tokens))

if __name__ == "__main__":
    main()
