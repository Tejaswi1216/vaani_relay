import sys
import json
from pathlib import Path
from datetime import datetime

def explain_slow(request_id: str, raw_dir_path: str = "results/raw"):
    raw_dir = Path(raw_dir_path)
    if not raw_dir.exists():
        print(f"Error: Raw directory '{raw_dir}' does not exist.")
        sys.exit(1)

    matched_record = None
    # Search all jsonl files in raw_dir
    for f in raw_dir.glob("*.jsonl"):
        with open(f, "r", encoding="utf-8") as jsonl_file:
            for line in jsonl_file:
                if not line.strip():
                    continue
                data = json.loads(line)
                if data.get("request_id") == request_id:
                    matched_record = data
                    break
        if matched_record:
            break

    if not matched_record:
        print(f"Error: Request ID '{request_id}' not found in {raw_dir}/*.jsonl")
        sys.exit(1)

    print(f"\n================================================================================")
    print(f"                     LATENCY EXPLANATION: {request_id}")
    print(f"================================================================================")
    
    total_ms = matched_record.get("total_ms", 0)
    first_token_ms = matched_record.get("first_token_ms")
    status = matched_record.get("status")
    scenario = matched_record.get("scenario")
    source = matched_record.get("source")
    queue_wait_ms = matched_record.get("queue_wait_ms", 0.0)
    attempts = matched_record.get("attempts", [])
    
    print(f"Scenario:    {scenario} ({source})")
    print(f"Status:      {status}")
    print(f"Total Time:  {total_ms} ms")
    print(f"First Token: {first_token_ms if first_token_ms is not None else 'N/A'} ms")
    print(f"Attempts:    {len(attempts)}")
    print(f"--------------------------------------------------------------------------------")

    backoff_ms_total = sum(a.get("backoff_sleep_ms", 0.0) for a in attempts)
    waiting_ms = queue_wait_ms + backoff_ms_total

    # Calculate working time (time inside HTTP calls)
    working_ms_total = 0.0
    retrying_working_ms = 0.0
    
    for idx, att in enumerate(attempts):
        send_ts_str = att.get("send_ts")
        end_ts_str = att.get("end_ts")
        att_duration_ms = 0.0
        if send_ts_str and end_ts_str:
            t_send = datetime.fromisoformat(send_ts_str).timestamp()
            t_end = datetime.fromisoformat(end_ts_str).timestamp()
            att_duration_ms = max(0.0, (t_end - t_send) * 1000.0)
        
        working_ms_total += att_duration_ms
        if idx < len(attempts) - 1:
            retrying_working_ms += att_duration_ms

    retrying_ms = retrying_working_ms + backoff_ms_total
    
    # Avoid zero division
    norm_total = max(1.0, float(total_ms))
    
    print(f"{'Phase / Component':<28} | {'Duration (ms)':<15} | {'Share of Total':<15}")
    print(f"--------------------------------------------------------------------------------")
    print(f"{'1. Waiting (Queue & Backoff)':<28} | {waiting_ms:<15.1f} | {waiting_ms/norm_total*100:<14.1f}%")
    print(f"   - Admission Queue Wait     | {queue_wait_ms:<15.1f} | {queue_wait_ms/norm_total*100:<14.1f}%")
    print(f"   - Retry Backoff Sleep      | {backoff_ms_total:<15.1f} | {backoff_ms_total/norm_total*100:<14.1f}%")
    print(f"{'2. Working (Model Calls)':<28} | {working_ms_total:<15.1f} | {working_ms_total/norm_total*100:<14.1f}%")
    print(f"{'3. Retrying (Failed attempts)':<28} | {retrying_ms:<15.1f} | {retrying_ms/norm_total*100:<14.1f}%")
    print(f"--------------------------------------------------------------------------------")

    # Where did delay come from: before or after first token?
    if first_token_ms is not None:
        after_first_token_ms = max(0, total_ms - first_token_ms)
        print(f"Timing relative to First Token:")
        print(f"  • Before First Token: {first_token_ms} ms ({first_token_ms/norm_total*100:.1f}%)")
        print(f"  • After First Token:  {after_first_token_ms} ms ({after_first_token_ms/norm_total*100:.1f}%)")
        
        if first_token_ms >= after_first_token_ms:
            print(f"\n=> PRIMARY DELAY: BEFORE first token.")
            print(f"   Explanation: Request accumulated delay during admission queueing ({queue_wait_ms:.0f}ms), "
                  f"retries ({retrying_ms:.0f}ms), and initial model TTFT before deterministic verification completed.")
        else:
            print(f"\n=> PRIMARY DELAY: AFTER first token.")
            print(f"   Explanation: Request experienced delay during token decode streaming across the network.")
    else:
        print(f"\n=> PRIMARY DELAY: BEFORE first token (No token ever delivered due to status: {status}).")

    print(f"================================================================================\n")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/explain_slow.py <request_id> [raw_dir]")
        sys.exit(1)
    req_id = sys.argv[1]
    raw_dir = sys.argv[2] if len(sys.argv) > 2 else "results/raw"
    explain_slow(req_id, raw_dir)
