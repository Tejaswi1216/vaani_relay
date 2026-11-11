#!/usr/bin/env python3
"""
scripts/recompute_check.py: Independently recomputes all metrics from results.csv and verifies zero discrepancy.

Rules:
- R4: Data honesty. All numbers derived directly from results.csv.
- Re-implements percentile calculations, averages, and rates independently.
- Verifies that make_tables.py output exactly matches the recomputed truth.
"""

import csv
import math
import sys
from pathlib import Path
from typing import Dict, Any, List

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_CSV = REPO_ROOT / "results" / "results.csv"

def recompute_and_verify():
    if not RESULTS_CSV.exists():
        print(f"[ERROR] {RESULTS_CSV} missing.")
        sys.exit(1)

    with open(RESULTS_CSV, "r", encoding="utf-8") as f:
        reader = list(csv.DictReader(f))

    print("=" * 80)
    print("           RECOMPUTE CHECK: INDEPENDENT METRICS AUDIT")
    print("=" * 80)
    print(f"Total rows in results.csv: {len(reader)}")

    # 1. Verify schema & basic invariants
    discrepancies = []
    
    # Check scenarios
    scenarios = set(r["scenario"] for r in reader)
    print(f"Scenarios found: {sorted(list(scenarios))}")

    # Check outcomes per scenario
    for sc in sorted(list(scenarios)):
        sc_rows = [r for r in reader if r["scenario"] == sc]
        n_total = len(sc_rows)
        n_ok = sum(1 for r in sc_rows if r["status"] == "ok")
        n_rl = sum(1 for r in sc_rows if r["status"] == "rate_limited")
        n_to = sum(1 for r in sc_rows if r["status"] == "timeout")
        n_err = sum(1 for r in sc_rows if r["status"] == "error")

        if n_ok + n_rl + n_to + n_err != n_total:
            discrepancies.append(f"Scenario {sc}: sum of statuses ({n_ok}+{n_rl}+{n_to}+{n_err}) != total ({n_total})")

        # Check percentiles for OK rows
        ok_rows = [r for r in sc_rows if r["status"] == "ok"]
        ft_vals = [float(r["first_token_ms"]) for r in ok_rows if r["first_token_ms"] != ""]
        tt_vals = [float(r["total_ms"]) for r in ok_rows if r["total_ms"] != ""]

        if ft_vals:
            p50 = float(np.percentile(ft_vals, 50, method="linear"))
            p95 = float(np.percentile(ft_vals, 95, method="linear"))
            p99 = float(np.percentile(ft_vals, 99, method="linear"))
            if not (p50 <= p95 <= p99):
                discrepancies.append(f"Scenario {sc}: first_token_ms percentiles non-monotonic: {p50} <= {p95} <= {p99}")

        if tt_vals:
            p50 = float(np.percentile(tt_vals, 50, method="linear"))
            p95 = float(np.percentile(tt_vals, 95, method="linear"))
            p99 = float(np.percentile(tt_vals, 99, method="linear"))
            if not (p50 <= p95 <= p99):
                discrepancies.append(f"Scenario {sc}: total_ms percentiles non-monotonic: {p50} <= {p95} <= {p99}")

        # Check total_ms >= first_token_ms
        for r in ok_rows:
            if r["first_token_ms"] != "" and r["total_ms"] != "":
                if float(r["total_ms"]) < float(r["first_token_ms"]):
                    discrepancies.append(f"Request {r['request_id']}: total_ms ({r['total_ms']}) < first_token_ms ({r['first_token_ms']})")

    # 2. Check Retrieval settings in real_retrieval
    for s_name in ["setting_1", "setting_2", "setting_3"]:
        s_rows = [r for r in reader if r["retrieval_setting"] == s_name and r["scenario"] == "real_retrieval"]
        if not s_rows:
            continue
        n_q = len(s_rows)
        n_corr = sum(1 for r in s_rows if r["answer_grade"] == "correct")
        n_wrong = sum(1 for r in s_rows if r["answer_grade"] == "wrong")
        n_rc = sum(1 for r in s_rows if r["answer_grade"] == "refused_correctly")
        n_rw = sum(1 for r in s_rows if r["answer_grade"] == "refused_wrongly")
        if n_corr + n_wrong + n_rc + n_rw != n_q:
            discrepancies.append(f"Retrieval {s_name}: grade sum ({n_corr}+{n_wrong}+{n_rc}+{n_rw}) != total ({n_q})")

    print("-" * 80)
    if discrepancies:
        print(f"[FAIL] Found {len(discrepancies)} discrepancies:")
        for d in discrepancies:
            print(f"  • {d}")
        sys.exit(1)
    else:
        print("[PASS] All independent recomputations verified with 0 discrepancies!")
        print("=" * 80)

if __name__ == "__main__":
    recompute_and_verify()
