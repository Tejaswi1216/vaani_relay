import json
import os
from datetime import datetime, timezone

LEDGER_PATH = os.path.expanduser("~/relay/results/ledger.jsonl")

def main():
    if not os.path.exists(LEDGER_PATH):
        print("Ledger empty or not found.")
        return
    
    total = 0
    today_total = 0
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    count = 0
    
    with open(LEDGER_PATH, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            tokens = d.get("total_tokens", 0)
            total += tokens
            ts = d.get("timestamp", "")
            if ts.startswith(today_str):
                today_total += tokens
            count += 1
            
    print(f"Total entries: {count}")
    print(f"Total tokens all time: {total}")
    print(f"Total tokens today ({today_str}): {today_total}")
    print(f"Remaining daily allowance (200k limit - 20k reserve = 180k max): {180000 - today_total}")

if __name__ == "__main__":
    main()
