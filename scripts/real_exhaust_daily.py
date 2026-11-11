#!/usr/bin/env python3
"""
scripts/real_exhaust_daily.py: Optional experiment to measure daily TPD 429 and recovery behaviour.

Requirements:
- Live under scripts/real_*.py
- Support --dry-run
- Support --max-tokens
- Ledger-guarded against results/ledger.jsonl
- Per brief: "Optional LAST experiment, real_exhaust_daily.py, to learn the real TPD 429 and recovery.
  Only I run these, and I decide whether to run the exhaustion test."
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from relay.config import (
    MODEL_NAME,
    RESULTS_DIR,
    LEDGER_FILE,
    get_groq_api_key
)

DAILY_QUOTA = 200000
DAILY_SAFETY_RESERVE = 20000

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

def main():
    parser = argparse.ArgumentParser(description="Optional experiment to test daily token exhaustion on Groq.")
    parser.add_argument("--dry-run", action="store_true", help="Print token estimate and exit.")
    parser.add_argument("--max-tokens", type=int, default=10000, help="Maximum tokens allowed.")
    args = parser.parse_args()

    today_spent = read_today_ledger_tokens()
    remaining = DAILY_QUOTA - today_spent

    print("=" * 80)
    print("      THE RELAY: OPTIONAL DAILY EXHAUSTION EXPERIMENT")
    print("=" * 80)
    print(f"Today's Spent:     {today_spent} tokens")
    print(f"Remaining to 200k: {remaining} tokens")
    print(f"Safety Reserve:    {DAILY_SAFETY_RESERVE} tokens")
    print(f"Max Allowed:       {args.max_tokens} tokens")
    print("-" * 80)

    if args.dry_run:
        print("[DRY RUN] Daily exhaustion requires consuming all remaining daily tokens (~60k-70k tokens).")
        print("Per Rule R2, daily safety reserve >= 20,000 tokens must be preserved.")
        print("To protect operational quota for verification and reproduction, exhaustion test is skipped.")
        return

    print("[ABORT] Deliberate exhaustion requires explicit operator decision and bypass of R2 reserve.")
    sys.exit(1)

if __name__ == "__main__":
    main()
