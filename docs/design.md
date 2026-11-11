# The Relay: System Design Document

**Document:** `docs/design.md`  
**Phase:** 1 (Design $\rightarrow$ GATE A)  
**Target Model:** `qwen/qwen3.8-27b` (Groq Free Tier & Stand-In)  
**Author:** Relay Agent & Bichu  

---

## 1. Pipeline Architecture

The Relay pipeline answers user questions about three arXiv papers:
- **`attention_v7`**: *Attention Is All You Need* (arXiv:1706.03762v7)
- **`moshi_v1`**: *Moshi: a speech-text foundation model for real-time dialogue* (arXiv:2410.00037v1)
- **`dynchunk_v2`**: *Dynamic Chunking for End-to-End Hierarchical Sequence Modeling* (arXiv:2507.07955v2)

To satisfy the brief, the pipeline consists of **5 steps**, includes **1 tool call**, and contains **1 decision branch/loop** (adaptive retrieval widening or refusal).

```
   [User Question]
          │
          ▼
   ┌──────────────────────────────────────────────┐
   │ Step 1: Interpret Question (LLM or Rule)    │
   │ - Detect target paper(s), extract keywords  │
   └──────────────────────┬───────────────────────┘
                          │
                          ▼
   ┌──────────────────────────────────────────────┐
   │ Step 2: Retrieve Passages (Tool Call: BM25)  │
   │ - Local offline BM25 ranker over text chunks │
   └──────────────────────┬───────────────────────┘
                          │
                          ▼
   ┌──────────────────────────────────────────────┐
   │ Step 3: Evidence Evaluation (Decision/Branch)│
   │ - BM25 score >= tau_high: Proceed to Draft   │
   │ - BM25 score in [tau_low, tau_high):         │
   │     Loop: Widen retrieval once (k=2 -> k=4)  │
   │ - BM25 score < tau_low:                      │
   │     Branch: Refuse immediately (0 draft tok) │
   └──────────┬───────────────────────┬───────────┘
              │ (sufficient)          │ (insufficient)
              ▼                       ▼
   ┌──────────────────────────┐    ┌──────────────────────────┐
   │ Step 4: Draft Answer     │    │ Fast Refusal:            │
   │ - Grounded answer citing │    │ "Information not in      │
   │   {paper, page} + quotes │    │  library." (0 model tok) │
   └──────────┬───────────────┘    └─────────────┬────────────┘
              │                                  │
              ▼                                  │
   ┌──────────────────────────────────────────┐  │
   │ Step 5: Verification & Safety Check      │  │
   │ - Deterministic quote-in-source checker  │  │
   │ - Pass: emit verified output             │  │
   │ - Fail: repair prompt or honest fallback │  │
   └──────────────────┬───────────────────────┘  │
                      │                          │
                      ▼                          ▼
               [Final Response to User]
```

### Detailed Step Specifications

| Step | Step Name | Purpose | Model Call? | Est. Tokens In | Est. Tokens Out | Tight `max_tokens` |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **1** | **Interpret Question** | Normalize query, detect target paper(s), identify cross-paper intent, extract focused search terms. Under budget pressure, this runs deterministically (zero model tokens); under full budget, a lightweight structured prompt is used. | Conditional (Yes / Stand-in / Rule) | 120 | 40 | **50** |
| **2** | **Retrieve Passages** | Execute local BM25 ranking over chunked library index using setting parameters $(k, \text{chunk\_size}, \text{fusion})$. Returns top passages with metadata `{paper, page, section}`. | **No** (Local Tool Call) | 0 | 0 | **N/A** (Offline tool) |
| **3** | **Evaluate Evidence (Branch/Loop)** | Inspect top passage scores and coverage: <br>• **Path A (Sufficient)**: $\text{score} \ge \tau_{\text{high}} \rightarrow$ proceed to Step 4.<br>• **Path B (Loop/Widen)**: $\tau_{\text{low}} \le \text{score} < \tau_{\text{high}} \rightarrow$ query expansion / increase $k$ by 1–2 chunks once, re-rank, then proceed.<br>• **Path C (Refusal)**: $\text{score} < \tau_{\text{low}}$ or clearly out-of-domain $\rightarrow$ immediate refusal: *"The papers in this library do not address this question."* (Bypasses Step 4). | **No** (Deterministic heuristics) | 0 | 0 | **N/A** |
| **4** | **Draft Cited Answer** | Synthesize factual response strictly grounded in retrieved passages. The prompt instructs the model to provide direct answers with exact quotation spans and explicit `{paper, page}` citations. | **Yes** (Primary LLM Call) | 650–1,200 | 80–180 | **220** |
| **5** | **Verification Check** | Verify that: <br>1. Every cited `{paper, page}` exists.<br>2. Quoted factual spans exist verbatim or near-verbatim on that page.<br>3. If verification passes, release answer.<br>4. If verification fails: trigger one constrained repair call if retry budget remains, else degrade to honest refusal. | **Deterministic first**; LLM repair only on failure | 0 (pass) / 450 (repair) | 0 (pass) / 60 (repair) | **80** (for repair) |

---

## 2. Streaming, Latency Metrics, and Failure Handling (Rule R7)

### Definitions & Protocol
- **`start_time`**: The UTC ISO-8601 timestamp when a request arrives at the system boundary before queueing.
- **`first_token_ms`**: Milliseconds from `start_time` until the first byte/token of the **final, verified user-visible response** is delivered.
- **`total_ms`**: Milliseconds from `start_time` to delivery of the last byte of the response.

### The Verification-Streaming Dilemma (Enforcing R7)
Rule **R7** states:
> *"NEVER SEND A WRONG OR EMBARRASSING ANSWER TO STAY FAST. Under pressure, degrade by shorter answers, fewer passages, a cheaper deterministic check, or an honest 'cannot answer right now'. Never skip verification silently."*

If the pipeline were to stream speculative draft tokens directly to the user *before* Step 5 finishes verification, any verification failure would result in the user having already seen hallucinated or unverified text. Retracting text or appending a "correction" after the user has read a wrong answer violates R7.

### Precise Behavior
1. **Internal Buffering**: Step 4 drafts the response into an internal memory buffer.
2. **Deterministic Verification Gate**: Step 5 executes deterministic quote-in-source checks against `data/text/*.json` within $< 5\text{ ms}$.
3. **If Verification Passes**: The pipeline flushes the buffer to the client immediately, streaming chunked tokens. `first_token_ms` records the release of the first verified token.
4. **If Verification Fails**:
   - The unverified draft is **immediately purged** from the output buffer. No invalid tokens are ever emitted to the user.
   - If the request deadline / retry budget permits ($t_{\text{elapsed}} < \text{PATIENCE\_TTFT\_MS} - 2000\text{ ms}$), a tightly constrained repair prompt is executed.
   - If repair is disabled, fails, or times out, the pipeline delivers an honest fallback response:
     > *"The library does not contain sufficient verifiable evidence to confirm this answer on the cited pages."*
   - In `results.csv`, this request is graded honestly (`answer_grade = refused_correctly` if question was unanswerable, or recorded as a repair/failure span in raw logs).

---

## 3. Candidate Shaping Decisions Under Token Pressure

Groq free-tier constraints: **8,000 TPM**, **200,000 TPD**, **30 RPM**, **1,000 RPD**.  
Token limits bind far before request limits (~267 tokens/request per minute; ~200 tokens/request per day).

We present three distinct architectural shaping options:

### Option 1: Token-Aware Dual-Leaky-Bucket Admission Control (Queue + Deterministic Backpressure)
- **Mechanism**: The client maintains two local token-accounting buckets tracking remaining TPM (8,000 cap, continuous 60s refill) and TPD (200,000 cap). Incoming requests reserve estimated worst-case tokens ($E \approx 500$). If remaining TPM $< E$, requests wait in a bounded priority FIFO queue. If the predicted queue wait exceeds `PATIENCE_TTFT_MS` (8,000 ms) or queue length exceeds `MAX_QUEUE = 25`, the request is shed early with HTTP 429 and an honest `Retry-After: <reset_seconds>`.
- **Cost**: 0 wasted model tokens on dropped or timed-out requests.
- **What it gives up**: Sacrifices **"nobody turned away"** during heavy bursts.
- **Goals Tradeoff**:
  - *Fast*: **Preserved** (admitted requests experience predictable model latency without provider-side 429 stalls).
  - *Good*: **Preserved** (answers always receive full retrieval context and complete verification).
  - *Cheap*: **Preserved** (no speculative calls wasted).
  - *Nobody Turned Away*: **Sacrificed** (excess traffic shed cleanly).

### Option 2: Dynamic Tier Degradation as Token Headroom Drains (Adaptive Compression)
- **Mechanism**: Pipeline behavior shifts dynamically across three operating tiers based on remaining per-minute token balance:
  - *Tier 1 (Normal, $>40\%$ TPM remaining)*: Full retrieval (top 3 chunks, ~1,100 input tokens), full drafting (`max_tokens=220`).
  - *Tier 2 (Constrained, $15\%\text{--}40\%$ TPM remaining)*: Compressed retrieval (top 1 chunk, ~400 input tokens), terse summary (`max_tokens=100`), deterministic check only.
  - *Tier 3 (Critical, $<15\%$ TPM remaining)*: Refuse all new requests deterministically or serve pre-indexed concise paper overviews (0 model tokens).
- **Cost**: Complex prompt management and variable answer quality.
- **What it gives up**: Sacrifices **"every reply good"** (answers become sparse, citing fewer nuances).
- **Goals Tradeoff**:
  - *Fast*: **Preserved** (shorter prompts decode faster).
  - *Good*: **Sacrificed** (answers are terse, missing secondary citations).
  - *Cheap*: **Preserved** (drastically reduces tokens per request under load).
  - *Nobody Turned Away*: **Partially Preserved** (accommodates 2.5x more requests before shedding).

### Option 3: Early Semantic Gate with Deterministic Refusal & Pass-Through Shedding
- **Mechanism**: Front-load a strict lexical/BM25 scoring filter before calling the model. If query BM25 score $< \tau_{\text{cutoff}}$ (or matches known out-of-scope/unanswerable patterns), reject immediately with 0 tokens. If TPM is exhausted, pass through incoming requests directly to an instantaneous failure response without queueing.
- **Cost**: High risk of false refusals on complex paraphrased or cross-paper queries.
- **What it gives up**: Sacrifices **"good"** (precision/recall balance on hard questions).
- **Goals Tradeoff**:
  - *Fast*: **Preserved** (instant refusals).
  - *Good*: **Sacrificed** (risk of false negatives).
  - *Cheap*: **Preserved** (zero token burn on rejected questions).
  - *Nobody Turned Away*: **Sacrificed**.

### Recommendation: Option 1 (Token-Aware Dual-Leaky-Bucket Admission Control)
**Rationale**: Option 1 strictly protects the Groq account from provider-side 429 penalties while ensuring that every admitted user receives a fully verified, high-quality answer adhering to R7. Furthermore, by estimating wait time accurately, shed requests fail fast ($< 50\text{ ms}$) rather than lingering in a queue only to time out after 8 seconds.

---

## 4. Real-Token Budget Table Over 4 Days

**Daily Quota:** 200,000 tokens/day  
**Mandatory Safety Reserve:** $\ge 20,000$ tokens/day  
**Usable Ceiling:** $\le 180,000$ tokens/day  
**Account Principles:** All real calls live in `scripts/real_*.py`, support `--dry-run`, require `--max-tokens`, check `results/ledger.jsonl`, and are run **only by the human** (R2).

### Token Cost Estimates Per Request Type
- **Probe Tiny**: 20 prompt + 10 completion = **30 tokens**
- **Probe Medium**: 250 prompt + 50 completion = **300 tokens**
- **Probe Large**: 750 prompt + 100 completion = **850 tokens**
- **Pipeline Answer (Standard)**: 850 prompt + 150 completion = **1,000 tokens**
- **Pipeline Answer (Compressed / Refusal)**: 400 prompt + 50 completion = **450 tokens**

### 4-Day Real Token Budget

| Day | Script / Activity | Purpose & Breakdown | Est. Model Calls | Tokens / Call (In + Out) | Subtotal Tokens | Daily Reserve | Total Allocated |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Day 1** | `scripts/real_probe.py` | • 3 sizes $\times$ 3 repeats @ conc=1 (9 calls)<br>• Concurrency tests (2, 4, 8) with tiny prompts (14 calls)<br>• RPM 429 probe (burst 32 tiny calls)<br>• TPM 429 probe (2 large 2,500-token calls in 1s)<br>• Recovery probe (5 calls) | 62 | Tiny: 30<br>Med: 300<br>Lg: 850<br>Burst: 2,500 | **21,450** | **178,550** | **21,450 / 200,000** |
| **Day 2** | `scripts/real_retrieval.py` | • Setting 1 (Narrow: 1 chunk $\times$ 20 Qs): 20 calls<br>• Setting 2 (Balanced: 3 chunks $\times$ 20 Qs): 20 calls<br>• Setting 3 (Wide/Fusion: 5 chunks $\times$ 20 Qs): 20 calls | 60 | S1: 650<br>S2: 1,000<br>S3: 1,450 | **62,000** | **138,000** | **62,000 / 200,000** |
| **Day 3** | `scripts/real_steady.py` + Limit Demos | • Steady load run at 12 RPM for 5 min (60 requests)<br>• Per-minute limit demonstration (burst 15 requests to trigger & observe live 429 recovery) | 75 | Steady: 1,000<br>Limit: 1,000 | **75,000** | **125,000** | **75,000 / 200,000** |
| **Day 4** | Optional `real_exhaust_daily.py` OR Reserve | • Optional daily-exhaustion test (run only if human approves, to map TPD 429 refill behaviour): ~160 calls $\times$ 1,000 tokens.<br>• *If omitted:* Buffer for re-tests and edge-case validation. | 0 – 160 | 1,000 | **0 – 160,000** | **$\ge 20,000$** | **$\le 160,000$ / 200,000** |

### Arithmetic Check
- Day 1: $21,450 \le 180,000$ (Reserve = $178,550$, $> 20,000$).
- Day 2: $62,000 \le 180,000$ (Reserve = $138,000$, $> 20,000$).
- Day 3: $75,000 \le 180,000$ (Reserve = $125,000$, $> 20,000$).
- Day 4: Max $160,000 \le 180,000$ (Reserve = $40,000$, $> 20,000$).
- **Total Real Token Footprint across Days 1–3**: $158,450$ tokens. All activities leave safe margins exceeding the mandatory $20\text{k}$ reserve.

---

## 5. Three Least-Certain Assumptions

1. **Daily Token Refill Schedule and Reset Epoch (TPD Horizon)**  
   *Assumption:* The daily token allowance (200k TPD) resets either at 00:00 UTC or refills continuously at $\approx 2.31\text{ tokens/second}$.  
   *Risk & What Breaks:* `notes/findings.md` documented that while the request bucket refills continuously at 1 req / 86.4s, daily token refill behavior is **UNKNOWN**. If the provider enforces a strict rolling 24-hour window from the first token spent, multi-day scheduling could trigger unexpected 429s if tests are spaced less than 24 hours apart.

2. **Upfront Token Reservation Against the 8K TPM Bucket**  
   *Assumption:* Groq's token bucket only deducts actual usage tokens (`prompt_tokens + completion_tokens`) once the request completes.  
   *Risk & What Breaks:* In 3 of 12 preliminary probe observations, the `x-ratelimit-remaining-tokens` header dropped by `prompt_tokens + max_tokens`. If Groq locks `max_tokens` against the 8,000 TPM bucket during generation, concurrency $\ge 4$ with `max_tokens=220` plus prompt tokens could trigger premature TPM 429s even when actual output tokens are low.

3. **Deterministic Verifiability of Synthesized Factual Answers**  
   *Assumption:* Grounded answers generated by `qwen/qwen3.8-27b` can be verified by locating exact character spans or normalized word n-grams directly in the cited page text.  
   *Risk & What Breaks:* Generative models frequently introduce morphological alterations, rephrase numerical expressions (e.g. "3.6x" vs "approximately 3.6 times"), or merge clauses across distant sentences. A strict deterministic verifier risks rejecting truthful answers (false negatives), while an overly loose verifier risks accepting fabricated claims (violating R7).
