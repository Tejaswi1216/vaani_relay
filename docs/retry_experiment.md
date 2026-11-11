# The Relay: Retry Experiment Report

**Document:** `docs/retry_experiment.md`  
**Phase:** 7 (Load Scenarios & Retry Experiment)  
**Workload:** Sustained Overload ($\ge 2\times$ capacity, offered rate: 60 req/min, 1 req/s)  

---

## 1. Quantitative Comparison: Our Policy vs. Naive Policy

| Metric | Our Policy (Admission Control + Jittered Backoff + `Retry-After`) | Naive Policy (Immediate Retry, Ignore `Retry-After`, 5 Attempts) | Relative Impact |
| :--- | :---: | :---: | :--- |
| **Offered Rate** | 60.0 req/min | 60.0 req/min | Identical load shape |
| **Total Requests ($n$)** | 60 | 60 | Identical workload |
| **Success %** | **20.0%** (12 / 60) | **16.7%** (10 / 60) | **+19.8% higher success** with our policy |
| **Rate-Limited % (429)** | 68.3% (41 / 60) | 83.3% (50 / 60) | Naive policy exhausted attempts on 429 |
| **Timeout %** | 11.7% (7 / 60) | 0.0% (0 / 60) | Our policy bounded patience before failing |
| **Retries per Request** | **2.45** | **3.33** | **+35.9% more wasted retry spam** under naive |
| **Peak Queue Length** | 3 | 0 (No admission buffer) | Our policy absorbed burst in queue |

---

## 2. Where Retrying Made Things Worse

The empirical data illustrates the classic **retry storm / thundering herd** failure mode:

1. **Self-Inflicted Denial of Service (Amplified Server Pressure):**
   When the token bucket emptied under the 60 req/min offered load, the naive policy immediately attempted re-transmission with zero delay, ignoring the provider's `retry-after` header. Because the bucket refills continuously over time, immediate re-attempts arrived before even a single token had replenished. This increased total network requests by **$3.33\times$ per client**, creating a thundering herd that kept the bucket permanently at 0 tokens.
   
2. **Burned Retry Budgets Without Refill:**
   Under the naive policy, requests burned through all 5 permitted attempts in under $5\text{ ms}$, failing with a hard 429 when waiting just 1–2 seconds would have allowed the token bucket to replenish.

3. **Contrast with Our Policy:**
   Our policy incorporated Option 1 admission control to gate requests before transmission, and used exponential backoff with full jitter clamped to the server's `retry-after` header. By respecting the provider's replenishment rate, our policy achieved a higher success rate while shedding unserviceable traffic early rather than hammering the server.
