#!/usr/bin/env python3
"""
scripts/make_tables.py: Generates Table 1, 2a, 2b, and 3 from results.csv and probe evidence.

Rules:
- R4: Every number comes from results.csv ONLY (except Table 1 evidence). Missing means "not measured".
- State n for every percentile; if n < 20, state that p95/p99 are unreliable.
- Percentiles: numpy.percentile(..., method="linear") over status == "ok" rows with values.
- Label every figure real or stand-in.
- Cost per 1,000 answers calculated strictly per notes/pricing.md ($0.80/1M in, $4.00/1M out for real; N/A for stand-in).
"""

import csv
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_CSV = REPO_ROOT / "results" / "results.csv"
RAW_DIR = REPO_ROOT / "results" / "raw"
FINDINGS_FILE = REPO_ROOT / "notes" / "findings.md"

def load_results_csv() -> List[Dict[str, Any]]:
    if not RESULTS_CSV.exists():
        print(f"[ERROR] {RESULTS_CSV} does not exist.")
        sys.exit(1)
    
    rows = []
    with open(RESULTS_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append({
                "request_id": r["request_id"],
                "scenario": r["scenario"],
                "source": r["source"],
                "start_time": r["start_time"],
                "first_token_ms": float(r["first_token_ms"]) if r["first_token_ms"] != "" else None,
                "total_ms": float(r["total_ms"]) if r["total_ms"] != "" else None,
                "status": r["status"],
                "retries": int(r["retries"]) if r["retries"] != "" else 0,
                "tokens_in": int(r["tokens_in"]) if r["tokens_in"] != "" else 0,
                "tokens_out": int(r["tokens_out"]) if r["tokens_out"] != "" else 0,
                "retrieval_setting": r["retrieval_setting"],
                "answer_grade": r["answer_grade"]
            })
    return rows

def compute_percentiles(values: List[float]) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    if not values:
        return None, None, None
    arr = np.array(values)
    p50 = float(np.percentile(arr, 50, method="linear"))
    p95 = float(np.percentile(arr, 95, method="linear"))
    p99 = float(np.percentile(arr, 99, method="linear"))
    return p50, p95, p99

def get_peak_queue(scenario: str) -> str:
    """Reads peak queue length from raw JSONL if available."""
    raw_path = RAW_DIR / f"{scenario}.jsonl"
    if not raw_path.exists():
        return "not measured"
    max_q = 0
    found = False
    with open(raw_path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                d = json.loads(line)
                if "queue_length_at_entry" in d:
                    max_q = max(max_q, d["queue_length_at_entry"])
                    found = True
            except Exception:
                continue
    return str(max_q) if found else "not measured"

def generate_table_1() -> str:
    """
    Table 1: Limits found in real service.
    Filled from notes/findings.md and results/raw/real_probe.jsonl with evidence sources.
    """
    lines = [
        "### Table 1: Measured Limits of Real Groq Service (`qwen/qwen3.8-27b`)",
        "",
        "| Limit / Parameter | Measured Value | Evidence Source | Confidence & Notes |",
        "|---|---:|---|---|",
        "| Requests per minute (RPM) | 30 | `notes/findings.md` §1, Groq headers | High (verified via `x-ratelimit-limit-requests` & 429 probe) |",
        "| Requests per day (RPD) | 1,000 | `notes/findings.md` §1, `x-ratelimit-limit-requests` | High (daily allowance header contract) |",
        "| Tokens per minute (TPM) | 8,000 | `notes/findings.md` §1, `x-ratelimit-limit-tokens` | High (binds before RPM; refills within ~70s) |",
        "| Tokens per day (TPD) | 200,000 | `notes/findings.md` §1 | High (published daily free-tier cap) |",
        "| In-flight concurrency | $\\ge 8$ concurrent | `results/raw/real_probe.jsonl` (Stage 2) | High (8 concurrent requests handled without connection drops) |",
        "| HTTP 429 Rate Limit Response | Header: `retry-after: 2` | `results/raw/real_probe.jsonl` (Stage 3) | High (captured exact header and JSON error payload) |",
        "| Rate Limit Recovery Window | 2.49 seconds | `results/raw/real_probe.jsonl` (Stage 3 recovery) | Measured on test account (cleared upon bucket refill) |",
        "| RPD Replenishment Model | ~1 req / 86.4 s | `notes/findings.md` §3 | Small sample (continuous leaky bucket replenishment) |",
        "| TPD Replenishment Model | not measured | `notes/findings.md` §3 | Unknown (daily reset vs continuous refill unmeasured) |",
        "| Warm TTFT | 300–500 ms (probe avg 603 ms) | `notes/findings.md` §4, `real_probe.jsonl` | Measured across 3 prompt sizes |",
        "| Decode Throughput | ~1,660 tokens/s | `results/raw/real_probe.jsonl` | Measured on LPUs (exceeds initial 500 tok/s estimate) |"
    ]
    return "\n".join(lines)

def generate_table_2a(rows: List[Dict[str, Any]]) -> str:
    """
    Table 2a: Latency percentiles across load scenarios.
    """
    scenarios = [
        ("steady", "stand-in"),
        ("burst", "stand-in"),
        ("sustained_overload", "stand-in"),
        ("per_minute_halved", "stand-in"),
        ("daily_budget_exhausted", "stand-in"),
        ("real_steady", "real"),
        ("real_limit_demo", "real")
    ]

    lines = [
        "### Table 2a: Latency Distribution Across Scenarios",
        "",
        "| Scenario | Source | Status OK ($n$) | First Token p50 (ms) | First Token p95 (ms) | First Token p99 (ms) | Total Time p50 (ms) | Total Time p95 (ms) | Total Time p99 (ms) | Reliability Note |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---|"
    ]

    for sc, src in scenarios:
        sc_rows = [r for r in rows if r["scenario"] == sc and r["source"] == src]
        ok_rows = [r for r in sc_rows if r["status"] == "ok"]
        n_ok = len(ok_rows)

        ft_vals = [r["first_token_ms"] for r in ok_rows if r["first_token_ms"] is not None]
        tt_vals = [r["total_ms"] for r in ok_rows if r["total_ms"] is not None]

        ft_p50, ft_p95, ft_p99 = compute_percentiles(ft_vals)
        tt_p50, tt_p95, tt_p99 = compute_percentiles(tt_vals)

        note = "Reliable ($n \\ge 20$)" if n_ok >= 20 else "p95/p99 unreliable ($n < 20$)"
        if n_ok == 0:
            note = "No OK requests (exhausted/shed)"

        def fmt(v):
            return f"{v:.1f}" if v is not None else "not measured"

        lines.append(
            f"| `{sc}` | {src} | {n_ok} | {fmt(ft_p50)} | {fmt(ft_p95)} | {fmt(ft_p99)} | {fmt(tt_p50)} | {fmt(tt_p95)} | {fmt(tt_p99)} | {note} |"
        )

    return "\n".join(lines)

def generate_table_2b(rows: List[Dict[str, Any]]) -> str:
    """
    Table 2b: Outcome and cost metrics across load scenarios.
    """
    scenarios = [
        ("steady", "stand-in"),
        ("burst", "stand-in"),
        ("sustained_overload", "stand-in"),
        ("per_minute_halved", "stand-in"),
        ("daily_budget_exhausted", "stand-in"),
        ("real_steady", "real"),
        ("real_limit_demo", "real")
    ]

    lines = [
        "### Table 2b: Request Outcomes and Operational Costs",
        "",
        "| Scenario | Source | Offered ($n$) | Succeeded % | Rate-Limited % | Timed Out % | Error % | Retries / Req | Peak Queue | Avg Tokens / Ans | Cost / 1,000 Answers (USD) |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"
    ]

    for sc, src in scenarios:
        sc_rows = [r for r in rows if r["scenario"] == sc and r["source"] == src]
        n_total = len(sc_rows)
        if n_total == 0:
            lines.append(f"| `{sc}` | {src} | 0 | not measured | not measured | not measured | not measured | not measured | not measured | not measured | not measured |")
            continue

        n_ok = sum(1 for r in sc_rows if r["status"] == "ok")
        n_rl = sum(1 for r in sc_rows if r["status"] == "rate_limited")
        n_to = sum(1 for r in sc_rows if r["status"] == "timeout")
        n_err = sum(1 for r in sc_rows if r["status"] == "error")

        pct_ok = (n_ok / n_total) * 100.0
        pct_rl = (n_rl / n_total) * 100.0
        pct_to = (n_to / n_total) * 100.0
        pct_err = (n_err / n_total) * 100.0

        avg_retries = sum(r["retries"] for r in sc_rows) / n_total
        peak_q = get_peak_queue(sc)

        ok_rows = [r for r in sc_rows if r["status"] == "ok"]
        if ok_rows:
            avg_tok = sum(r["tokens_in"] + r["tokens_out"] for r in ok_rows) / len(ok_rows)
            # Cost per 1,000 answers per notes/pricing.md ($0.80/1M in, $4.00/1M out)
            if src == "real":
                tot_in = sum(r["tokens_in"] for r in ok_rows)
                tot_out = sum(r["tokens_out"] for r in ok_rows)
                cost_1k = 1000.0 * ((tot_in * 0.80 + tot_out * 4.00) / (len(ok_rows) * 1_000_000.0))
                cost_str = f"${cost_1k:.4f}"
            else:
                cost_str = "N/A (stand-in)"
            tok_str = f"{avg_tok:.1f}"
        else:
            tok_str = "not measured"
            cost_str = "not measured"

        lines.append(
            f"| `{sc}` | {src} | {n_total} | {pct_ok:.1f}% | {pct_rl:.1f}% | {pct_to:.1f}% | {pct_err:.1f}% | {avg_retries:.2f} | {peak_q} | {tok_str} | {cost_str} |"
        )

    return "\n".join(lines)

def generate_table_3(rows: List[Dict[str, Any]]) -> str:
    """
    Table 3: Retrieval settings evaluation.
    """
    settings_meta = [
        ("setting_1", 200, 2, "None (BM25 only)"),
        ("setting_2", 400, 3, "None (BM25 only)"),
        ("setting_3", 300, 3, "Term-density fusion")
    ]

    lines = [
        "### Table 3: Retrieval Settings Comparison (`source=real`)",
        "",
        "| Setting | Chunk Size (words) | $k$ | Reranking | Questions ($n$) | Correct & Cited % | Wrong % | Refused Correctly % | Refused Wrongly % | Avg Tokens / Ans | First Token p50 (ms) | First Token p95 (ms) | Total Time p50 (ms) | Total Time p95 (ms) |",
        "|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"
    ]

    for s_name, chunk_sz, k_val, rerank_desc in settings_meta:
        s_rows = [r for r in rows if r["retrieval_setting"] == s_name and r["source"] == "real" and r["scenario"] == "real_retrieval"]
        n_q = len(s_rows)
        if n_q == 0:
            lines.append(f"| `{s_name}` | {chunk_sz} | {k_val} | {rerank_desc} | 0 | not measured | not measured | not measured | not measured | not measured | not measured | not measured | not measured | not measured |")
            continue

        n_corr = sum(1 for r in s_rows if r["answer_grade"] == "correct")
        n_wrong = sum(1 for r in s_rows if r["answer_grade"] == "wrong")
        n_rc = sum(1 for r in s_rows if r["answer_grade"] == "refused_correctly")
        n_rw = sum(1 for r in s_rows if r["answer_grade"] == "refused_wrongly")

        pct_corr = (n_corr / n_q) * 100.0
        pct_wrong = (n_wrong / n_q) * 100.0
        pct_rc = (n_rc / n_q) * 100.0
        pct_rw = (n_rw / n_q) * 100.0

        avg_tok = sum(r["tokens_in"] + r["tokens_out"] for r in s_rows) / n_q

        ft_vals = [r["first_token_ms"] for r in s_rows if r["first_token_ms"] is not None]
        tt_vals = [r["total_ms"] for r in s_rows if r["total_ms"] is not None]

        ft_p50, ft_p95, _ = compute_percentiles(ft_vals)
        tt_p50, tt_p95, _ = compute_percentiles(tt_vals)

        def fmt(v):
            return f"{v:.1f}" if v is not None else "not measured"

        lines.append(
            f"| `{s_name}` | {chunk_sz} | {k_val} | {rerank_desc} | {n_q} | {pct_corr:.1f}% | {pct_wrong:.1f}% | {pct_rc:.1f}% | {pct_rw:.1f}% | {avg_tok:.1f} | {fmt(ft_p50)} | {fmt(ft_p95)} | {fmt(tt_p50)} | {fmt(tt_p95)} |"
        )

    return "\n".join(lines)

def main():
    rows = load_results_csv()
    print("=" * 80)
    print("                 THE RELAY: BENCHMARK RESULTS TABLES")
    print("=" * 80)
    print()
    print(generate_table_1())
    print("\n" + "-" * 80 + "\n")
    print(generate_table_2a(rows))
    print("\n" + "-" * 80 + "\n")
    print(generate_table_2b(rows))
    print("\n" + "-" * 80 + "\n")
    print(generate_table_3(rows))
    print("\n" + "=" * 80)

if __name__ == "__main__":
    main()
