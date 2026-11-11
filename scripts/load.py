#!/usr/bin/env python3
"""
scripts/load.py: Executes load scenarios on the stand-in model service.

Scenarios:
1. steady: ~60% capacity (18 RPM, 1 req every 3.33s), n >= 100
2. burst: 10x capacity for 30s (5 req/s = 150 reqs), then 30s quiet
3. sustained_overload_ours: 2x capacity (60 RPM = 1 req/s) for 5 min (300 reqs) with our policy
4. sustained_overload_naive: 2x capacity (60 RPM = 1 req/s) for 5 min (300 reqs) with naive policy
5. per_minute_halved: steady load against TPM halved (TPM=4000)
6. daily_exhausted: TPD set low (15000 tokens) to exhaust mid-run
"""

import argparse
import asyncio
import json
import random
import time
import uuid
from pathlib import Path
from typing import Dict, Any, List, Optional
import httpx
import yaml

from standin.app import app, state
from relay.client_standin import StandinClient
from relay.pipeline import RelayPipeline
from relay.policy import RetryPolicy, AdmissionController
from relay.metrics import MetricsLogger, RequestRecord, AttemptSpan
from relay.config import QUESTIONS_FILE, RESULTS_CSV, RESULTS_DIR

def load_questions():
    with open(QUESTIONS_FILE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

class NaiveClient(StandinClient):
    """Client implementing the naive retry policy: immediate retry, ignore retry-after, 5 attempts."""
    async def chat_completion(self, messages, max_tokens=220, policy=None):
        url = f"{self.base_url}/chat/completions"
        body = {"model": "qwen/qwen3.8-27b", "messages": messages, "stream": True, "max_tokens": max_tokens}
        attempts = []
        overall_start = time.perf_counter()

        for attempt_idx in range(5):
            from datetime import datetime, timezone
            send_ts = datetime.now(timezone.utc).isoformat()
            span = AttemptSpan(attempt_idx=attempt_idx, send_ts=send_ts)
            attempts.append(span)

            t0 = time.perf_counter()
            first_byte_time = None
            content_chunks = []
            tokens_in = 0
            tokens_out = 0

            try:
                async with self._client.stream("POST", url, json=body) as resp:
                    span.status_code = resp.status_code
                    span.headers = {k.lower(): v for k, v in resp.headers.items()}
                    span.end_ts = datetime.now(timezone.utc).isoformat()

                    if resp.status_code == 429:
                        # Naive: ignores retry-after, immediate retry without sleep
                        span.retry_after = None
                        span.backoff_sleep_ms = 0.0
                        continue

                    if resp.status_code >= 400:
                        span.error = f"HTTP {resp.status_code}"
                        continue

                    async for line in resp.aiter_lines():
                        if line.startswith("data: ") and line != "data: [DONE]":
                            payload = json.loads(line[6:])
                            ch = payload.get("choices") or []
                            if ch:
                                content = ch[0].get("delta", {}).get("content")
                                if content:
                                    if first_byte_time is None:
                                        first_byte_time = time.perf_counter()
                                        span.first_byte_ts = datetime.now(timezone.utc).isoformat()
                                    content_chunks.append(content)
                            usage = payload.get("x_groq", {}).get("usage")
                            if usage:
                                tokens_in = usage.get("prompt_tokens", 0)
                                tokens_out = usage.get("completion_tokens", 0)

                    full_answer = "".join(content_chunks).strip()
                    first_token_ms = ((first_byte_time - t0) * 1000.0) if first_byte_time else None
                    if tokens_in == 0:
                        tokens_in = len(" ".join(m["content"] for m in messages).split())
                        tokens_out = len(full_answer.split())
                    return full_answer, first_token_ms, tokens_in, tokens_out, attempts, "ok"

            except Exception as e:
                span.end_ts = datetime.now(timezone.utc).isoformat()
                span.error = str(e)
                continue

        return None, None, 0, 0, attempts, "rate_limited"

async def run_scenario(
    scenario_name: str,
    duration_s: float,
    rate_per_sec: float,
    policy_type: str = "ours",
    standin_params_override: Optional[dict] = None,
    seed: int = 42
):
    print("\n" + "=" * 80)
    print(f"RUNNING SCENARIO: {scenario_name} (Policy: {policy_type}, Rate: {rate_per_sec*60:.0f} req/min, Target Duration: {duration_s}s)")
    print("=" * 80)

    rng = random.Random(seed)
    questions = load_questions()

    # Reset stand-in and apply parameters
    state.reset_buckets()
    with open("standin/params.json", "r") as f:
        params = json.load(f)
    if standin_params_override:
        params.update(standin_params_override)
    state.update_params(params)

    transport = httpx.ASGITransport(app=app)
    async_client = httpx.AsyncClient(transport=transport, base_url="http://test/v1")
    
    if policy_type == "naive":
        client = NaiveClient(base_url="http://test/v1", client=async_client)
        admission = None  # No admission control for naive policy
    else:
        client = StandinClient(base_url="http://test/v1", client=async_client)
        admission = AdmissionController(
            tpm_capacity=params.get("limits", {}).get("tpm", 8000),
            tpd_capacity=params.get("limits", {}).get("tpd", 200000),
            max_queue_size=30
        )

    logger = MetricsLogger()
    pipeline = RelayPipeline(
        client=client,
        admission_controller=admission,
        metrics_logger=logger,
        source="stand-in"
    )

    peak_queue_length = 0
    tasks = []
    start_time = time.perf_counter()
    req_idx = 0

    async def worker(q_item, r_id):
        nonlocal peak_queue_length
        if admission:
            current_q = admission.queue_length
            if current_q > peak_queue_length:
                peak_queue_length = current_q
        
        record = await pipeline.answer_question(
            question=q_item["question"],
            request_id=r_id,
            scenario=scenario_name,
            retrieval_setting="setting_2",
            gold_data=q_item
        )
        return record

    interval = 1.0 / rate_per_sec if rate_per_sec > 0 else 1.0
    end_by = start_time + duration_s

    while time.perf_counter() < end_by:
        q_item = rng.choice(questions)
        req_idx += 1
        r_id = f"{scenario_name}_{req_idx:04d}_{uuid.uuid4().hex[:6]}"
        t = asyncio.create_task(worker(q_item, r_id))
        tasks.append(t)
        
        # Pace requests
        next_send = start_time + (req_idx * interval)
        sleep_dur = next_send - time.perf_counter()
        if sleep_dur > 0:
            await asyncio.sleep(sleep_dur)

    print(f"Sent {len(tasks)} requests. Waiting for in-flight requests to complete...")
    results = await asyncio.gather(*tasks, return_exceptions=True)
    await client.close()

    # Tally metrics
    valid_records = [r for r in results if isinstance(r, RequestRecord)]
    succeeded = sum(1 for r in valid_records if r.status == "ok")
    rate_limited = sum(1 for r in valid_records if r.status == "rate_limited")
    timeouts = sum(1 for r in valid_records if r.status == "timeout")
    total_reqs = len(valid_records)
    total_retries = sum(r.retries for r in valid_records)

    print("-" * 80)
    print(f"Scenario Finished: {scenario_name}")
    print(f"  Total Requests (n): {total_reqs}")
    print(f"  Succeeded:          {succeeded} ({succeeded/max(total_reqs, 1)*100:.1f}%)")
    print(f"  Rate Limited (429): {rate_limited} ({rate_limited/max(total_reqs, 1)*100:.1f}%)")
    print(f"  Timeouts:           {timeouts} ({timeouts/max(total_reqs, 1)*100:.1f}%)")
    print(f"  Retries / Request:  {total_retries / max(total_reqs, 1):.2f}")
    print(f"  Peak Queue Length:  {peak_queue_length}")
    print("=" * 80)

async def main():
    parser = argparse.ArgumentParser(description="Run stand-in load scenarios")
    parser.add_argument("--scenario", choices=["all", "steady", "burst", "sustained", "halved", "daily_exhausted"], default="all")
    parser.add_argument("--fast", action="store_true", help="Run shortened duration for rapid verification")
    args = parser.parse_args()

    # Time scale multipliers: fast mode speeds up for testing; standard meets brief requirements
    dur_scale = 0.2 if args.fast else 1.0

    scenarios_to_run = []
    if args.scenario in ["all", "steady"]:
        # Steady: 18 RPM (~60% of 30 RPM capacity). Duration: 340s * scale -> n=102 requests
        scenarios_to_run.append(("steady", 340.0 * dur_scale, 18.0 / 60.0, "ours", None))
    
    if args.scenario in ["all", "burst"]:
        # Burst: 10x capacity (300 RPM = 5 req/s) for 30s = 150 requests
        scenarios_to_run.append(("burst", 30.0, 5.0, "ours", None))
    
    if args.scenario in ["all", "sustained"]:
        # Sustained overload: 2x capacity (60 RPM = 1 req/s) for 300s (5 min) = 300 requests
        # Run twice: once with our policy, once with naive policy
        scenarios_to_run.append(("sustained_overload", 300.0 * dur_scale, 1.0, "ours", None))
        scenarios_to_run.append(("sustained_overload_naive", 300.0 * dur_scale, 1.0, "naive", None))

    if args.scenario in ["all", "halved"]:
        # Per-minute limit halved: steady load against TPM=4000
        halved_override = {"limits": {"tpm": 4000, "rpm": 30, "rpd": 1000, "tpd": 200000}}
        scenarios_to_run.append(("per_minute_halved", 340.0 * dur_scale, 18.0 / 60.0, "ours", halved_override))

    if args.scenario in ["all", "daily_exhausted"]:
        # Daily budget exhausted: TPD set low (15,000 tokens) to exhaust mid-run
        daily_exhaust_override = {"limits": {"tpm": 8000, "rpm": 30, "rpd": 1000, "tpd": 15000, "tpd_continuous_refill_tokens_per_sec": 0.0}}
        scenarios_to_run.append(("daily_budget_exhausted", 200.0 * dur_scale, 30.0 / 60.0, "ours", daily_exhaust_override))

    for name, dur, rate, pol, override in scenarios_to_run:
        await run_scenario(
            scenario_name=name,
            duration_s=dur,
            rate_per_sec=rate,
            policy_type=pol,
            standin_params_override=override
        )

if __name__ == "__main__":
    asyncio.run(main())
