# The Relay: Multi-Step LLM Pipeline with Rate-Limit Resilience

"The Relay" is an asynchronous question-answering pipeline answering questions about three arXiv research papers (`attention_v7`, `moshi_v1`, `dynchunk_v2`) with deterministic citation verification, operated against Groq Cloud (`qwen/qwen3.8-27b`) and a calibrated FastAPI stand-in model service.

---

## 1. Quickstart & Environment Setup

This project requires **Python 3.10+**. The active virtual environment is located at `.venv`.

```bash
# Activate existing virtual environment
source .venv/bin/activate

# Install dependencies (if recreating environment)
pip install -r requirements.txt
```

### Groq API Key Setup (Rule R1)
The Groq API key is stored strictly outside the repository at `~/.config/relay/groq.env`:
```bash
# Format of ~/.config/relay/groq.env:
GROQ_API_KEY=gsk_your_key_here
```
No environment files or API keys are ever stored in the workspace or printed to logs.

---

## 2. Reproducing Benchmark Tables (Phase 10 & 12)

All benchmark tables (Table 1, Table 2a, Table 2b, Table 3) are generated directly from `results/results.csv` and probe evidence:

```bash
# Generate all tables in Markdown format
PYTHONPATH=. .venv/bin/python scripts/make_tables.py

# Independently verify all calculations and schema invariants
PYTHONPATH=. .venv/bin/python scripts/recompute_check.py

# Validate results.csv against schema rules (no missing rows, valid enums)
PYTHONPATH=. .venv/bin/python scripts/validate_results.py
```

---

## 3. Running Unit and End-to-End Tests

```bash
# Run the full 22-test pytest suite
.venv/bin/pytest
```

Test coverage includes:
- `tests/test_chunker.py`: Page-aware chunking and metadata preservation.
- `tests/test_verifier.py`: Deterministic quote-in-source verification and refusal detection.
- `tests/test_retrieval.py`: BM25 retrieval hits and term-density fusion.
- `tests/test_standin.py`: Stand-in streaming, rate-limit buckets (TPM/RPM), and failure injection.
- `tests/test_pipeline_e2e.py`: 5-step pipeline execution, R7 verified flush, and retry policies.

---

## 4. Reproducing Stand-in Load Scenarios (Phase 7)

To run the stand-in model service and execute load scenarios:

```bash
# Start the calibrated stand-in service (runs on localhost:8000)
PYTHONPATH=. .venv/bin/python -m uvicorn standin.app:app --port 8000 &

# Run individual load scenarios (writes to results/results.csv)
PYTHONPATH=. .venv/bin/python scripts/load.py --scenario steady
PYTHONPATH=. .venv/bin/python scripts/load.py --scenario burst
PYTHONPATH=. .venv/bin/python scripts/load.py --scenario sustained_overload
PYTHONPATH=. .venv/bin/python scripts/load.py --scenario sustained_overload_naive
PYTHONPATH=. .venv/bin/python scripts/load.py --scenario per_minute_halved
PYTHONPATH=. .venv/bin/python scripts/load.py --scenario daily_budget_exhausted
```

### Explaining a Slow Request
```bash
# Decompose latency into waiting (queue/backoff) vs working time
PYTHONPATH=. .venv/bin/python scripts/explain_slow.py steady_0005_a685cb
```

---

## 5. Running Real API Scripts (Rules R1 & R2)

All scripts that spend real Groq tokens live under `scripts/real_*.py`, support `--dry-run`, take `--max-tokens`, guard against `results/ledger.jsonl`, and preserve a $\ge 20,000$ token daily safety reserve.

```bash
# 1. Real Probe (concurrency, latency distribution, rate-limit discovery)
PYTHONPATH=. .venv/bin/python scripts/real_probe.py --dry-run
PYTHONPATH=. .venv/bin/python scripts/real_probe.py --max-tokens 25000

# 2. Real Retrieval Evaluation (evaluates settings 1, 2, 3 across all 20 questions)
PYTHONPATH=. .venv/bin/python scripts/real_retrieval.py --dry-run
PYTHONPATH=. .venv/bin/python scripts/real_retrieval.py --max-tokens 75000

# 3. Real Steady Run & Per-Minute Limit Demo
PYTHONPATH=. .venv/bin/python scripts/real_steady.py --dry-run
PYTHONPATH=. .venv/bin/python scripts/real_steady.py --max-tokens 30000

# Check real token expenditure in ledger
PYTHONPATH=. .venv/bin/python scripts/check_ledger.py
```

---

## 6. Repository Layout

```
relay/          # Core package: pipeline, policy, retrieval, library, verifier, metrics, clients
standin/        # FastAPI stand-in model service and calibration parameters (params.json)
scripts/        # Benchmark, reproduction, and operational scripts
tests/          # Unit and integration test suite (22 tests)
results/        # Benchmark dataset (results.csv, ledger.jsonl, and raw/*.jsonl)
docs/           # Design, decisions, calibration, retry analysis, and technical writeup
questions/      # 20 benchmark questions (questions.yaml)
data/           # Paper PDFs (data/papers/) and extracted JSON text (data/text/)
notes/          # Initial findings and pricing documentation
```
