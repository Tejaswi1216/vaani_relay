import csv
import json
import os
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional
from relay.config import RESULTS_CSV, RESULTS_DIR

CSV_COLUMNS = [
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

@dataclass
class AttemptSpan:
    attempt_idx: int
    send_ts: str
    first_byte_ts: Optional[str] = None
    end_ts: Optional[str] = None
    status_code: Optional[int] = None
    headers: Dict[str, str] = field(default_factory=dict)
    retry_after: Optional[float] = None
    backoff_sleep_ms: float = 0.0
    error: Optional[str] = None

@dataclass
class RequestRecord:
    request_id: str
    scenario: str
    source: str                 # "real" | "stand-in"
    start_time: str             # UTC ISO-8601
    first_token_ms: Optional[int]
    total_ms: int
    status: str                 # "ok" | "rate_limited" | "timeout" | "error"
    retries: int
    tokens_in: int
    tokens_out: int
    retrieval_setting: str
    answer_grade: Optional[str] = None # "correct" | "wrong" | "refused_correctly" | "refused_wrongly" | ""
    
    # Detailed spans and telemetry
    queue_wait_ms: float = 0.0
    queue_length_at_entry: int = 0
    attempts: List[AttemptSpan] = field(default_factory=list)
    raw_response_text: Optional[str] = None
    verification_errors: List[str] = field(default_factory=list)

class MetricsLogger:
    def __init__(self, raw_dir: Optional[Path] = None):
        self.raw_dir = raw_dir or (RESULTS_DIR / "raw")
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        self._ensure_csv_header()

    def _ensure_csv_header(self):
        if not RESULTS_CSV.exists() or RESULTS_CSV.stat().st_size == 0:
            with open(RESULTS_CSV, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(CSV_COLUMNS)

    def log_request(self, rec: RequestRecord):
        # 1. Append single row to results.csv
        row = [
            rec.request_id,
            rec.scenario,
            rec.source,
            rec.start_time,
            "" if rec.first_token_ms is None else rec.first_token_ms,
            rec.total_ms,
            rec.status,
            rec.retries,
            rec.tokens_in,
            rec.tokens_out,
            rec.retrieval_setting,
            rec.answer_grade or ""
        ]
        with open(RESULTS_CSV, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(row)

        # 2. Append full structured JSONL record to results/raw/<scenario>.jsonl
        scenario_file = self.raw_dir / f"{rec.scenario}.jsonl"
        with open(scenario_file, "a", encoding="utf-8") as f:
            # Convert dataclass to dict
            data = asdict(rec)
            f.write(json.dumps(data) + "\n")
