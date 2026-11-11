# The Relay: Project Progress

## Phase 0: Preflight
- **Status:** Completed
- **Verification:**
  - Brief PDF: Verified (`docs/The_Relay_Assignment_Brief.pdf`).
  - Papers & Checksums: Verified `attention_v7.pdf`, `moshi_v1.pdf`, `dynchunk_v2.pdf` against `data/papers/SHA256SUMS` (all OK).
  - Extracted Text: Extracted per-page text into `data/text/{attention_v7,moshi_v1,dynchunk_v2}.json` via PyMuPDF4LLM.
  - Quality Inspection: Analyzed tables, equations, two-column flow, and header/footers with concrete examples and page references.
  - Notes: `notes/findings.md` and `notes/pricing.md` verified.
  - Questions: `questions/questions.yaml` verified (20 questions, answerable + unanswerable + cross-paper, gold answers intact).
  - Security: Verified no `gsk_` strings in repo; no `.env` files in workspace.
- **Git Commit:** `6859eda`

## Phase 1: Design -> GATE A
- **Status:** Completed & Approved
- **Approvals (GATE A):**
  - Shaping Decision: Option 1 (Token-Aware Dual-Leaky-Bucket Admission Control).
  - Protocol: Internal buffering with verified flush to guarantee R7 compliance.
  - Budget: 4-day budget approved (Day 1: 21,450, Day 2: 62,000, Day 3: 75,000, Day 4: $\le 160,000$; reserve $\ge 20,000$/day).
- **Git Commit:** `26ed8c0`

## Phase 2: Library and Retrieval (Offline, 0 real tokens)
- **Status:** Completed
- **Deliverables:**
  - `relay/library.py`: Page-aware chunker with metadata `{paper, version, page, section}`.
  - `relay/retrieval.py`: BM25 indexer with 3 configurable retrieval settings and term-density fusion re-ranking.
  - `relay/verifier.py`: Deterministic citation parser and quote-in-source verifier with normalization.
  - `tests/`: 14 pytest unit tests (`test_chunker.py`, `test_verifier.py`, `test_retrieval.py`) all passing.
  - `docs/misleading_retrieval.md`: Documented three concrete ways the library misleads a naive retriever (`q07` jargon saturation, `q01` formula degradation, `q20` trap vocabulary).
- **Git Commit:** `0e95a07`

## Phase 3: Stand-in Model Service (FastAPI, Localhost)
- **Status:** Completed
- **Deliverables:**
  - `standin/params.json`: Provisional calibration parameters derived from AGENTS.md Section 3.
  - `standin/app.py`: FastAPI OpenAI-compatible streaming chat completions service (`POST /v1/chat/completions`) with limits (TPM, RPM, RPD, TPD), latency model, chunked SSE streaming, failure injection, and admin endpoints.
  - `tests/test_standin.py`: Integration tests covering streaming, TPM/RPM rate limits, error injection, and metrics.
- **Git Commit:** `48ca002`

## Phase 4: Pipeline, Policy, Metrics (Against Stand-in Only)
- **Status:** Completed
- **Deliverables:**
  - `relay/policy.py`: Retry policy + Option 1 Admission Controller (dual-leaky-bucket with bounded FIFO queue).
  - `relay/client_standin.py`: Asynchronous streaming HTTP client collecting per-attempt spans.
  - `relay/pipeline.py`: Full 5-step pipeline with internal buffering, R7 verification, and graceful fallback.
  - `relay/metrics.py`: Metrics logger writing strictly formatted `results/results.csv` and structured `results/raw/*.jsonl`.
  - `scripts/explain_slow.py`: Diagnostic tool splitting latency into waiting, working, and retrying, and before vs. after first token.
  - `scripts/validate_results.py`: Schema validator verifying columns, enums, uniqueness, and raw JSONL parity.
  - `tests/test_pipeline_e2e.py`: End-to-end tests covering success flow, 429 backoff/retry, timeouts, and validation.
  - Total test suite: 22 tests passing (100%).
- **Git Commit:** `25b8c5a`

## Phase 5: Real Probe Script -> GATE B
- **Status:** Completed (Authorized by human)
- **Execution:** Ran `scripts/real_probe.py --max-tokens 25000`. Captured real latency across 3 sizes, concurrency 2/4/8, RPM 429 header contract (`retry-after: 2`), TPM 429, and recovery time (2.49s). Usage recorded in `results/ledger.jsonl`.
- **Git Commit:** `138d317`

## Phase 6: Stand-in Calibration
- **Status:** Completed
- **Deliverables:**
  - `scripts/calibrate_standin.py`: Analyzed raw probe telemetry and updated `standin/params.json`.
  - `scripts/compare_standin_real.py`: Benchmarked stand-in against real probe data on identical prompt workloads.
  - `docs/calibration.md`: Authored calibration report with real vs. stand-in p50/p95/p99 latency tables, sample sizes ($n$), fitting methods, and honest analysis of architectural differences.
- **Git Commit:** `3cac64f`

## Phase 7: Load Scenarios on the Stand-in
- **Status:** Completed
- **Deliverables:**
  - `scripts/load.py`: Implemented and executed load scenarios: steady, burst, sustained overload (our policy vs. naive policy), per-minute limit halved, and daily budget exhausted.
  - `docs/slow_request_explanation.txt`: Diagnostic output of `explain_slow.py` on slow request decomposing waiting (queue + backoff) vs working time.
  - `docs/retry_experiment.md`: Comparative analysis of naive vs. backoff retry policies under sustained overload, highlighting the thundering herd failure mode.
  - Schema integrity: `validate_results.py` verified 391 rows in `results/results.csv` with 100% schema compliance and zero missing rows.
## Phase 8: Real Retrieval Comparison
- **Status:** Completed
- **Deliverables:**
  - `scripts/real_retrieval.py`: Executed 60 real queries (20 questions across `setting_1`, `setting_2`, and `setting_3`) against Groq `qwen/qwen3.8-27b`.
  - Auto-graded every question against `must_contain` facts, citation pages, and refusal criteria.
  - Telemetry and grades written to `results/raw/real_retrieval.jsonl`, `results/raw/grades.jsonl`, `results/ledger.jsonl`, and updated `results/results.csv`.
- **Git Commit:** `34fdd32`

## Phase 9: Real Steady Run and Limit Demos -> GATE D
- **Status:** Completed (Authorized by human)
- **Deliverables:**
  - `scripts/real_steady.py`: Real steady-load run (10 questions spaced ~12s) and concurrent burst limit demonstration (5 concurrent requests) against real Groq service.
  - Succeeded 100% on steady run (p50 TTFT 1,352.5 ms); demonstrated HTTP 429 capture, backoff, and recovery during burst.
  - `scripts/real_exhaust_daily.py`: Implemented optional daily exhaustion script with `--dry-run`, `--max-tokens`, and ledger guarding (skipped to preserve operational safety reserve per R2).
  - All real token usage tracked in `results/ledger.jsonl` (spent 138,867 tokens; preserved 41,133 usable allowance above 20k safety reserve).
  - Data appended to `results/results.csv` and `results/raw/real_steady.jsonl`, `results/raw/real_limit_demo.jsonl`.

## Phase 10: Merge and Tables
- **Status:** Completed
- **Deliverables:**
  - Validated unified `results/results.csv` (466 rows total, 100% schema compliant, 0 missing rows).
  - `scripts/make_tables.py`: Generates Table 1 (measured limits), Table 2a (latency percentiles with sample sizes $n$ and reliability caveats), Table 2b (request outcomes, peak queue, tokens/ans, cost per 1,000 answers per `notes/pricing.md`), and Table 3 (retrieval settings comparison).
  - `scripts/recompute_check.py`: Independently recalculates all metrics and asserts 0 discrepancies against `results.csv`.

## Phase 11: Write-up Draft
- **Status:** Completed
- **Deliverables:**
  - `docs/writeup.md`: Comprehensive 2-page technical report adhering strictly to required headings in exact order:
    1. *What I built and why*
    2. *The real service and my stand-in* (including Table 1)
    3. *Results* (including Tables 2a and 2b)
    4. *Where the time goes*
    5. *When a limit is hit*
    - *Retrieval: quality versus cost* (including Table 3)
    - *How this library will mislead a naive retriever*
    6. *Retries*
    7. *What the person sees* (exact 3pm exhaustion response text and line reference)
    8. *What I'm unsure about, what I left out, what I learned* (one-line prompt for author)
    - *Your feedback on Vaani* (one-line prompt for author)
    - *How I worked with the assistant* (one-line prompt for author)
    - *Audit of Claims Not Directly Backed by Files or Numbers*

## Phase 12: Pre-submission Checks & Packaging
- **Status:** Completed
- **Verification:**
  - `scripts/validate_results.py`: 466 rows validated, 100% compliant.
  - `scripts/recompute_check.py`: Passed with 0 discrepancies.
  - `pytest`: 22/22 unit and integration tests passing.
  - Secret scan: 0 occurrences of `gsk_` found across all workspace files and logs.
  - `README.md`: Complete guide with reproduction commands for all tables and scenarios.
  - `submission/`: Self-contained submission directory packaged with code, results, raw data, docs, questions, notes, and conversation logs.
