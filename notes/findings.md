
# Groq API Findings

**Last updated:** 2026-11-09  
**Primary model:** `qwen/qwen3.8-27b`  
**Provider:** GroqCloud  
**Evidence:** Account measurements recorded in AGENTS.md Section 3.

> These are small-sample observations from the account used for this
> project. They are not universal guarantees or production benchmarks.

## 1. Rate Limits

The following limits were observed for the selected model and the
two GPT-OSS models on the account.

| Limit | Observed value |
|---|---:|
| Requests per minute (RPM) | 30 |
| Requests per day (RPD) | 1,000 |
| Tokens per minute (TPM) | 8,000 |
| Tokens per day (TPD) | 200,000 |

Token limits bind before request limits when requests contain
sufficiently large token volumes.

Break-even token consumption:

- Per minute: 8,000 / 30 = approximately 267 tokens/request.
- Per day: 200,000 / 1,000 = 200 tokens/request.

For a multi-stage LLM pipeline, every model call contributes to
token consumption. One user request can therefore consume quota
through several model calls.

## 2. Rate-Limit Headers

The following response headers were observed:

- `x-ratelimit-limit-requests`
- `x-ratelimit-remaining-requests`
- `x-ratelimit-reset-requests`
- `x-ratelimit-limit-tokens`
- `x-ratelimit-remaining-tokens`
- `x-ratelimit-reset-tokens`

Observed behaviour:

- Request headers represent the daily request allowance.
- Token headers represent the per-minute token allowance.
- `retry-after` was observed on HTTP 429 responses. The response
  body containing it has not yet been captured.

### Important accounting observation

The remaining-tokens header is not consistently equal to the
actual remaining allowance.

In 3 out of 12 observations, it appeared to represent
prompt tokens plus the requested maximum output tokens.
Other observations were approximately consistent with actual usage.

**Decision for accounting:** Use the API response's `usage` fields
rather than relying on remaining-token headers.

## 3. Refill Behaviour

### Daily request allowance

The request budget appeared to refill continuously.

One clean test observed approximately:

- One request replenished every 86.4 seconds.
- Two requests replenished in 203.9 seconds.
- The `reset-requests` header appeared consistent with:

  `deficit × 86.4 seconds`

This is a small-sample observation, not a guaranteed refill
contract.

### Per-minute token allowance

The token bucket appeared to refill within seconds.

It was observed to be full after 70 seconds of inactivity.

The exact refill function has not been established.

### Daily token allowance

Daily token refill behaviour remains UNKNOWN.

Do not assume that daily token replenishment follows the same
pattern as daily request replenishment.

## 4. Latency and Throughput Observations

Measurements recorded:

| Metric | Observation |
|---|---:|
| Warm TTFT | 300–500 ms |
| Cold-connection TTFT | 900–1,150 ms |
| Decode throughput | Approximately 500 tokens/s |
| Near-zero-load queue time | 49–251 ms |

The decoding observation was approximately 43 tokens in 82 ms.

No reasoning deltas were observed in the tested responses.

These are calibration observations, not load-test results.
They must not be presented as steady-load p95 or p99 measurements.

## 5. HTTP Client Reuse

Creating a new HTTP client for every request introduced an
observed overhead of approximately 600–800 ms.

The implementation should use:

- One long-lived `httpx.AsyncClient`.
- Asynchronous HTTP requests.
- Shared connection pooling.
- Explicit connection and request timeouts.

The client should be closed cleanly when the application shuts down.

## 6. Implications for The Relay

The following implementation considerations follow from the
measurements:

1. Token consumption must be monitored across all stages of a
   single user request.

2. Output limits should be kept tight to prevent unnecessary
   token consumption.

3. One shared asynchronous HTTP client should be used to avoid
   repeated connection setup overhead.

4. Rate-limit responses should be handled explicitly, including
   `Retry-After` when available.

5. Retries must be bounded and included in per-request metrics.

6. The pipeline must retain an honest failure/refusal path when
   quota or latency constraints prevent successful completion.

7. All actual token accounting must use returned usage fields.

8. The stand-in should be used for development and load testing.
   Real Groq calls must be restricted to human-run scripts.

## 7. Unverified or Unknown Behaviour

The following remain unmeasured:

- Exact daily token refill behaviour.
- Complete response-body behaviour for HTTP 429.
- Behaviour at sustained near-limit request concurrency.
- Behaviour under long-running concurrent requests.
- Reasoning-token accounting, if applicable.
- Production-quality p95 and p99 under different load shapes.

These should remain explicitly labelled as unmeasured until
corresponding experiments are performed.

## 8. Evidence Limitations

All measurements above are transcribed from the account's
recorded observations in AGENTS.md Section 3.

The sample sizes are small. They should not be generalized into
provider-wide guarantees.

For reproducibility, future observations should reference the
corresponding request IDs and raw JSONL probe records.

Do not silently replace these observations with assumptions.
