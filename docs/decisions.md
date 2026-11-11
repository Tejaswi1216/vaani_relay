# The Relay: Decisions Log

All architectural decisions with real trade-offs are logged here in accordance with AGENTS.md Rule R9.

---

## Decision Log

### Decision 1: Behaviour Under Token Pressure (Approved at GATE A)
- **Status:** Approved by Bichu (2026-11-09)
- **Options Presented:**
  1. **Option 1: Token-Aware Dual-Leaky-Bucket Admission Control**
     - *Cost:* 0 extra model tokens; turns away excess requests with HTTP 429 and estimated reset delay.
     - *Sacrifices:* "Nobody turned away" under sustained overload.
     - *Preserves:* "Fast" (predictable low wait), "Good" (uncompromised context/verification), "Cheap" (0 wasted tokens).
  2. **Option 2: Dynamic Tier Degradation as Token Headroom Drains**
     - *Cost:* Multi-tier prompt management; reduced answer depth under load.
     - *Sacrifices:* "Every reply good" (shorter answers, fewer citations).
     - *Preserves:* "Fast" (shorter decode), "Cheap" (lower token burn), accommodates 2.5x more requests.
  3. **Option 3: Early Semantic Gate with Deterministic Refusal & Pass-Through Shedding**
     - *Cost:* Higher false-refusal rate on difficult/borderline questions.
     - *Sacrifices:* "Good" (precision on paraphrased/cross-paper questions).
     - *Preserves:* "Cheap" (0 model tokens on refusals), "Fast" (instant rejection).
- **Chosen:** **Option 1 (Token-Aware Dual-Leaky-Bucket Admission Control)**
- **Who Chose:** Bichu (User)
- **Cost / Tradeoff:** Turns away excess traffic when TPM/TPD queues are saturated, guaranteeing that admitted requests never face provider-side 429 throttles or verification degradations.

---

### Decision 2: Streaming vs. Verification Protocol (Rule R7 Alignment)
- **Status:** Approved by Bichu (2026-11-09)
- **Options Presented:**
  1. **Speculative Streaming to User:** Stream output directly while generating; if verification fails, append disclaimer or retraction.
     - *Sacrifices:* Violates R7 ("Never send a wrong or embarrassing answer to stay fast").
  2. **Internal Buffering with Verified Flush:** Buffer generation in memory; run deterministic quote-in-source verification; flush verified tokens immediately.
     - *Sacrifices:* TTFT includes full completion decode time (~100–300 ms overhead), but guarantees 100% adherence to R7.
- **Chosen:** **Option 2 (Internal Buffering with Verified Flush)**
- **Who Chose:** Bichu (User)
- **Cost / Tradeoff:** Output generation latency is shifted before the first visible user token. Emitted output is strictly verified against source text.

---

### Decision 3: Real Token 4-Day Budget
- **Status:** Approved by Bichu (2026-11-09)
- **Allocation:**
  - Day 1: 21,450 tokens (Reserve: 178,550 tokens)
  - Day 2: 62,000 tokens (Reserve: 138,000 tokens)
  - Day 3: 75,000 tokens (Reserve: 125,000 tokens)
  - Day 4: 0 – 160,000 tokens (Reserve $\ge 20,000$ tokens)
- **Who Chose:** Bichu (User)
- **Cost / Tradeoff:** Hard ceiling $\le 180,000$ tokens/day, preserving $\ge 20,000$ tokens daily safety reserve against Groq account quotas.
