import sys
import csv
import json
from pathlib import Path
from datetime import datetime

EXPECTED_COLUMNS = [
    "request_id",
    "scenario",
    "source",
    "start_time",
    "first_token_ms",
    "total_ms",
    "status",
    "retries",
    "tokens_in",
    "tokens_out",
    "retrieval_setting",
    "answer_grade"
]

VALID_SOURCES = {"real", "stand-in"}
VALID_STATUSES = {"ok", "rate_limited", "timeout", "error"}
VALID_GRADES = {"correct", "wrong", "refused_correctly", "refused_wrongly", ""}

def validate_results(csv_path_str: str = "results/results.csv", raw_dir_str: str = "results/raw") -> bool:
    csv_path = Path(csv_path_str)
    raw_dir = Path(raw_dir_str)
    errors = []

    print(f"\n================================================================================")
    print(f"                     VALIDATING RESULTS: {csv_path}")
    print(f"================================================================================")

    if not csv_path.exists():
        print(f"Error: Results file '{csv_path}' does not exist.")
        return False

    seen_request_ids = set()
    row_count = 0

    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header is None:
            print("Error: CSV file is empty.")
            return False

        if header != EXPECTED_COLUMNS:
            errors.append(f"Header mismatch.\nExpected: {EXPECTED_COLUMNS}\nFound:    {header}")

        for line_idx, row in enumerate(reader, start=2):
            row_count += 1
            if len(row) != len(EXPECTED_COLUMNS):
                errors.append(f"Line {line_idx}: Column count mismatch (expected {len(EXPECTED_COLUMNS)}, got {len(row)})")
                continue

            (req_id, scenario, source, start_time, first_token_ms, total_ms,
             status, retries, tokens_in, tokens_out, retrieval_setting, answer_grade) = row

            # 1. request_id uniqueness
            if not req_id:
                errors.append(f"Line {line_idx}: Empty request_id")
            elif req_id in seen_request_ids:
                errors.append(f"Line {line_idx}: Duplicate request_id '{req_id}'")
            seen_request_ids.add(req_id)

            # 2. Source enum
            if source not in VALID_SOURCES:
                errors.append(f"Line {line_idx} [{req_id}]: Invalid source '{source}' (expected one of {VALID_SOURCES})")

            # 3. Status enum
            if status not in VALID_STATUSES:
                errors.append(f"Line {line_idx} [{req_id}]: Invalid status '{status}' (expected one of {VALID_STATUSES})")

            # 4. Answer grade enum
            if answer_grade not in VALID_GRADES:
                errors.append(f"Line {line_idx} [{req_id}]: Invalid answer_grade '{answer_grade}' (expected one of {VALID_GRADES})")

            # 5. Timestamp format
            try:
                datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            except Exception:
                errors.append(f"Line {line_idx} [{req_id}]: Invalid ISO-8601 start_time '{start_time}'")

            # 6. Numeric constraints
            try:
                tot = int(total_ms)
                if tot < 0:
                    errors.append(f"Line {line_idx} [{req_id}]: total_ms must be >= 0")
            except ValueError:
                errors.append(f"Line {line_idx} [{req_id}]: total_ms '{total_ms}' is not an integer")

            if first_token_ms != "":
                try:
                    ft = int(first_token_ms)
                    if ft < 0:
                        errors.append(f"Line {line_idx} [{req_id}]: first_token_ms must be >= 0")
                except ValueError:
                    errors.append(f"Line {line_idx} [{req_id}]: first_token_ms '{first_token_ms}' is not an integer or empty")

            try:
                if int(retries) < 0:
                    errors.append(f"Line {line_idx} [{req_id}]: retries must be >= 0")
            except ValueError:
                errors.append(f"Line {line_idx} [{req_id}]: retries '{retries}' is not an integer")

            try:
                if int(tokens_in) < 0 or int(tokens_out) < 0:
                    errors.append(f"Line {line_idx} [{req_id}]: tokens_in/tokens_out must be >= 0")
            except ValueError:
                errors.append(f"Line {line_idx} [{req_id}]: tokens_in/tokens_out are not integers")

    # Cross-reference with raw JSONL files if raw_dir exists
    raw_request_ids = set()
    if raw_dir.exists():
        for jsonl_file in raw_dir.glob("*.jsonl"):
            with open(jsonl_file, "r", encoding="utf-8") as rf:
                for rline in rf:
                    if not rline.strip():
                        continue
                    try:
                        record = json.loads(rline)
                        raw_id = record.get("request_id")
                        if raw_id:
                            raw_request_ids.add(raw_id)
                    except json.JSONDecodeError:
                        errors.append(f"Corrupt JSON line in {jsonl_file.name}")

        missing_in_csv = raw_request_ids - seen_request_ids
        missing_in_raw = seen_request_ids - raw_request_ids

        if missing_in_csv:
            errors.append(f"{len(missing_in_csv)} request(s) found in raw JSONL but missing in results.csv: {list(missing_in_csv)[:5]}")
        if missing_in_raw:
            errors.append(f"{len(missing_in_raw)} request(s) found in results.csv but missing in raw JSONL: {list(missing_in_raw)[:5]}")

    print(f"Total Rows Validated: {row_count}")
    print(f"Unique Request IDs:   {len(seen_request_ids)}")

    if errors:
        print(f"\nVALIDATION FAILED with {len(errors)} error(s):")
        for err in errors[:15]:
            print(f"  • {err}")
        if len(errors) > 15:
            print(f"  ... and {len(errors) - 15} more.")
        print(f"================================================================================\n")
        return False
    else:
        print("\nSUCCESS: results.csv conforms 100% to schema, enums, and raw integrity checks.")
        print(f"================================================================================\n")
        return True

if __name__ == "__main__":
    csv_file = sys.argv[1] if len(sys.argv) > 1 else "results/results.csv"
    raw_folder = sys.argv[2] if len(sys.argv) > 2 else "results/raw"
    is_valid = validate_results(csv_file, raw_folder)
    sys.exit(0 if is_valid else 1)
