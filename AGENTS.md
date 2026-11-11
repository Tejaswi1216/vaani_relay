# The Relay: project rules for coding agents

Read this entire file before every task. These rules override convenience and speed.

## 1. Project
Build "The Relay": a small multi-step LLM pipeline answering questions about three arXiv papers,
with citations, using Groq's free tier (model: qwen/qwen3.8-27b) plus a stand-in model service
I control, then run it under several load shapes and write up the results.
The brief is docs/The_Relay_Assignment_Brief.pdf and is authoritative. Python 3.10+, venv at .venv.
Library: attention_v7 (arXiv 1706.03762 v7), moshi_v1 (2410.00037 v1), dynchunk_v2 (2507.07955 v2).
Extracted per-page text is in data/text/*.json. Checksums are in data/papers/SHA256SUMS.

## 2. Hard rules (never violate)
R1 SECRETS. The Groq key lives only in ~/.config/relay/groq.env. Never read, print, copy, log, grep
   or ask for it. Never run printenv/env/cat on env files. Load it only inside relay/config.py.
R2 REAL QUOTA IS SCARCE (200,000 tokens/day, 8,000 tokens/min, 1,000 requests/day, 30 requests/min).
   All development and testing runs against the stand-in. Any script that spends real tokens must:
   live under scripts/real_*.py; support --dry-run (prints estimated tokens, makes no call);
   take --max-tokens; refuse to start if results/ledger.jsonl shows less allowance than its estimate;
   append every real call's usage to results/ledger.jsonl. The HUMAN runs real scripts. You never do.
R3 NO TRICKS. One account, one primary model. Do not rotate models or keys to gain quota.
R4 DATA HONESTY. Never invent, estimate, round or "typically" a number. Every number in tables or
   the write-up comes from scripts/make_tables.py reading results.csv. Missing means "not measured".
   Label every figure real or stand-in. State n for every percentile; if n < 20, say p95/p99 are unreliable.
R5 ONE ROW PER REQUEST in results.csv, including failures and given-up requests. Never drop rows.
R6 LIBRARY FIDELITY. Answers cite paper + page (+ section if known). If the papers do not say, the
   reply states that plainly. Never guess.
R7 NEVER SEND A WRONG OR EMBARRASSING ANSWER TO STAY FAST. Under pressure, degrade by shorter answers,
   fewer passages, a cheaper deterministic check, or an honest "cannot answer right now". Never skip
   verification silently.
R8 EXPLAIN. After each phase, summarise: what was built, what it costs, what was given up, how to run
   it, how it was tested. I must be able to defend and modify every line live.
R9 DECISIONS ARE MINE. For any design choice with a real tradeoff, present 2-3 options with costs, give a
   recommendation, then STOP. Do not decide silently. Log each in docs/decisions.md
   (decision, options, who chose, cost).
R10 DO NOT MODIFY: questions/questions.yaml, docs/, data/papers/, anything in results/raw/real_*.
   Raw files are append-only.
R11 SAFETY. No sudo. No commands outside ~/relay. Tell me before installing any package and add it to
   requirements.txt. No network except the Groq client (human-run) and localhost.
R12 If unsure, ask (max 3 questions) rather than assume.

## 3. Measured facts (2026-11-09, my account, small n; treat as dated evidence)
- Limits page, qwen/qwen3.8-27b, gpt-oss-20b, gpt-oss-120b: 30 RPM, 1K RPD, 8K TPM, 200K TPD.
- Token limits bind before request limits (break-even 267 tokens/request per minute, 200 per day).
- Headers: x-ratelimit-{limit,remaining,reset}-{requests,tokens}; requests headers = per day,
  tokens headers = per minute. retry-after seen only on 429 (body not yet captured).
- Daily request budget refills continuously, about 1 request per 86.4 s (+2 in 203.9 s, one clean test).
  reset-requests = deficit x 86.4 s. TPD refill behaviour: UNKNOWN.
- Per-minute token bucket refills within seconds (full after 70 s idle).
- Decode about 500 tokens/s (43 tokens in 82 ms). Warm TTFT about 300-500 ms; cold connection
  about 900-1150 ms. usage.queue_time was 49-251 ms at near-zero load. No reasoning deltas seen.
- Remaining-tokens header sometimes equals prompt+max_tokens (3 of 12), otherwise about actual usage.
  Use the usage field for accounting. Always set tight max_tokens.
- Reuse one long-lived httpx.AsyncClient; a new client per request adds about 600-800 ms.

## 4. Definitions (I may edit before Phase 1)
- start_time: when a request enters the system, before any queueing (UTC ISO-8601).
- first_token_ms: start_time to the first content token of the FINAL reply shown to the user.
  If a draft streams before verification finishes, document what happens if verification fails.
- total_ms: start_time to the last byte of the final reply.
- Given up (status=timeout): PATIENCE_TTFT_MS = 8000 or PATIENCE_TOTAL_MS = 30000.   [EDIT]
- "Fast" target for steady load: p95 first_token_ms <= 3000.                          [EDIT]
- tokens_in / tokens_out: sums of usage fields over all model calls in the request.
- retries: number of re-attempts across all calls in the request.
- Percentiles: numpy.percentile(..., method="linear"). Latency percentiles over status == ok rows
  that have a value; always report n.
- Cost per 1,000 answers: tokens x price in notes/pricing.md; otherwise "not measured".

## 5. results.csv (exactly these columns, in this order)
request_id,scenario,source,start_time,first_token_ms,total_ms,status,retries,tokens_in,tokens_out,retrieval_setting,answer_grade
source: real | stand-in. status: ok | rate_limited | timeout | error.
answer_grade: correct | wrong | refused_correctly | refused_wrongly (empty for load-only runs).
Extra detail (spans, headers, queue length) goes in results/raw/*.jsonl keyed by request_id.

## 6. Layout
relay/ (package: config, library, retrieval, pipeline, client_real, client_standin, policy, metrics)
standin/ (FastAPI service + params.json), scripts/ (real_*.py, load.py, make_tables.py,
explain_slow.py, validate_results.py), tests/, results/ (results.csv, raw/, ledger.jsonl),
docs/ (design.md, decisions.md, calibration.md, writeup.md), questions/, notes/, data/.

## 7. Code standards
asyncio + one shared httpx.AsyncClient; type hints; pytest for library, stand-in, policy, metrics;
seeded randomness (record seeds); structured JSONL logging with per-attempt spans
(queue_wait, send_ts, first_byte_ts, end_ts, outcome, retry_after, backoff_sleep);
no secrets, no absolute paths in committed code; commit after each phase.

## 8. Working agreement
Work in phases. At every GATE: stop, summarise, list what I must do, wait for my reply.
Keep docs/PROGRESS.md updated after each phase.
