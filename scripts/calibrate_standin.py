import json
import numpy as np
from pathlib import Path
from datetime import datetime

def load_probe_data(raw_file: str = "results/raw/real_probe.jsonl"):
    records = []
    with open(raw_file, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                d = json.loads(line)
                if "label" in d:
                    records.append(d)
    return records

def compute_percentiles(values):
    if not values:
        return None, None, None
    arr = np.array(values)
    p50 = float(np.percentile(arr, 50, method="linear"))
    p95 = float(np.percentile(arr, 95, method="linear"))
    p99 = float(np.percentile(arr, 99, method="linear"))
    return p50, p95, p99

def analyze_real_probe():
    records = load_probe_data()
    print(f"Loaded {len(records)} probe records.")

    # Group by label/stage
    s1_small_ttft = [r["first_token_ms"] for r in records if "s1_small" in r["label"] and r["status"] == "ok" and r["first_token_ms"] is not None]
    s1_med_ttft = [r["first_token_ms"] for r in records if "s1_med" in r["label"] and r["status"] == "ok" and r["first_token_ms"] is not None]
    s1_large_ttft = [r["first_token_ms"] for r in records if "s1_large" in r["label"] and r["status"] == "ok" and r["first_token_ms"] is not None]

    s1_small_total = [r["total_ms"] for r in records if "s1_small" in r["label"] and r["status"] == "ok"]
    s1_med_total = [r["total_ms"] for r in records if "s1_med" in r["label"] and r["status"] == "ok"]
    s1_large_total = [r["total_ms"] for r in records if "s1_large" in r["label"] and r["status"] == "ok"]

    conc2_ttft = [r["first_token_ms"] for r in records if "s2_conc2" in r["label"] and r["status"] == "ok" and r["first_token_ms"] is not None]
    conc4_ttft = [r["first_token_ms"] for r in records if "s2_conc4" in r["label"] and r["status"] == "ok" and r["first_token_ms"] is not None]
    conc8_ttft = [r["first_token_ms"] for r in records if "s2_conc8" in r["label"] and r["status"] == "ok" and r["first_token_ms"] is not None]

    all_warm_ttft = s1_small_ttft + s1_med_ttft + s1_large_ttft
    mean_ttft = float(np.mean(all_warm_ttft)) if all_warm_ttft else 380.0
    std_ttft = float(np.std(all_warm_ttft)) if all_warm_ttft else 80.0

    print("--- REAL PROBE STATS ---")
    print(f"Warm TTFT: mean={mean_ttft:.1f}ms, std={std_ttft:.1f}ms (n={len(all_warm_ttft)})")
    print(f"Conc 1 TTFT mean: {np.mean(s1_small_ttft):.1f}ms")
    print(f"Conc 2 TTFT mean: {np.mean(conc2_ttft):.1f}ms")
    print(f"Conc 4 TTFT mean: {np.mean(conc4_ttft):.1f}ms")
    print(f"Conc 8 TTFT mean: {np.mean(conc8_ttft):.1f}ms")

    # Estimate decode speed from large prompt
    # total_ms - ttft_ms gives generation time for tokens_out
    decode_rates = []
    for r in records:
        if r["status"] == "ok" and r["first_token_ms"] and r["tokens_out"] > 10:
            gen_time_s = (r["total_ms"] - r["first_token_ms"]) / 1000.0
            if gen_time_s > 0:
                decode_rates.append(r["tokens_out"] / gen_time_s)
    
    mean_decode = float(np.mean(decode_rates)) if decode_rates else 500.0
    print(f"Fitted decode rate: {mean_decode:.1f} tokens/s (n={len(decode_rates)})")

    # Concurrency factor: ratio of conc8 / conc1
    ratio = (np.mean(conc8_ttft) / np.mean(s1_small_ttft)) if s1_small_ttft and conc8_ttft else 2.0
    slowdown_factor = float((ratio - 1.0) / 8.0)
    print(f"Fitted concurrency slowdown factor: {slowdown_factor:.3f}")

    # Update standin/params.json
    params_file = Path("standin/params.json")
    with open(params_file, "r") as f:
        params = json.load(f)

    params["latency"]["warm_ttft_ms_mean"] = round(mean_ttft, 1)
    params["latency"]["warm_ttft_ms_std"] = round(std_ttft, 1)
    params["latency"]["decode_tokens_per_sec"] = round(mean_decode, 1)
    params["latency"]["concurrency_slowdown_factor"] = round(slowdown_factor, 3)

    with open(params_file, "w") as f:
        json.dump(params, f, indent=2)
    print("Updated standin/params.json with fitted parameters.")

    return {
        "s1_small_ttft": s1_small_ttft,
        "s1_med_ttft": s1_med_ttft,
        "s1_large_ttft": s1_large_ttft,
        "s1_small_total": s1_small_total,
        "s1_med_total": s1_med_total,
        "s1_large_total": s1_large_total,
        "conc2_ttft": conc2_ttft,
        "conc4_ttft": conc4_ttft,
        "conc8_ttft": conc8_ttft,
    }

if __name__ == "__main__":
    analyze_real_probe()
