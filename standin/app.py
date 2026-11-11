import asyncio
import json
import math
import random
import time
import uuid
from pathlib import Path
from typing import Dict, Any, Optional, List

from fastapi import FastAPI, Request, Response, HTTPException, status
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel, Field

PARAMS_PATH = Path(__file__).resolve().parent / "params.json"

def load_params() -> dict:
    if PARAMS_PATH.exists():
        with open(PARAMS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

class Bucket:
    """Token/Request Leaky Bucket with continuous refill."""
    def __init__(self, capacity: float, refill_rate_per_sec: float, initial_val: Optional[float] = None):
        self.capacity = float(capacity)
        self.refill_rate = float(refill_rate_per_sec)
        self.current = float(capacity) if initial_val is None else float(initial_val)
        self.last_update = time.monotonic()

    def update(self) -> float:
        now = time.monotonic()
        delta = now - self.last_update
        self.last_update = now
        self.current = min(self.capacity, self.current + delta * self.refill_rate)
        return self.current

    def try_consume(self, amount: float) -> bool:
        self.update()
        if self.current >= amount:
            self.current -= amount
            return True
        return False

    def time_until(self, amount: float) -> float:
        self.update()
        if self.current >= amount:
            return 0.0
        deficit = amount - self.current
        return deficit / self.refill_rate if self.refill_rate > 0 else 60.0

class StandinState:
    def __init__(self):
        self.params = load_params()
        self.rng = random.Random(self.params.get("seed", 42))
        self.in_flight = 0
        self.total_requests = 0
        self.total_tokens_in = 0
        self.total_tokens_out = 0
        self.rate_limited_count = 0
        self.reset_buckets()

    def reset_buckets(self):
        limits = self.params.get("limits", {})
        # TPM: refills over 60 seconds
        tpm = limits.get("tpm", 8000)
        self.tpm_bucket = Bucket(capacity=tpm, refill_rate_per_sec=tpm / 60.0)

        # RPM: refills over 60 seconds
        rpm = limits.get("rpm", 30)
        self.rpm_bucket = Bucket(capacity=rpm, refill_rate_per_sec=rpm / 60.0)

        # RPD: refills 1 request per rpd_refill_interval_seconds (86.4s)
        rpd = limits.get("rpd", 1000)
        rpd_interval = limits.get("rpd_refill_interval_seconds", 86.4)
        rpd_refill_rate = 1.0 / rpd_interval if rpd_interval > 0 else (rpd / 86400.0)
        self.rpd_bucket = Bucket(capacity=rpd, refill_rate_per_sec=rpd_refill_rate)

        # TPD: refills continuous at ~2.315 tokens/sec
        tpd = limits.get("tpd", 200000)
        tpd_rate = limits.get("tpd_continuous_refill_tokens_per_sec", 200000.0 / 86400.0)
        self.tpd_bucket = Bucket(capacity=tpd, refill_rate_per_sec=tpd_rate)

    def update_params(self, new_params: dict):
        self.params.update(new_params)
        if "seed" in new_params:
            self.rng = random.Random(new_params["seed"])
        self.reset_buckets()

state = StandinState()
app = FastAPI(title="The Relay Stand-in Model Service", version="1.0.0")

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatCompletionRequest(BaseModel):
    model: str = "qwen/qwen3.8-27b"
    messages: List[ChatMessage]
    stream: bool = False
    max_tokens: Optional[int] = 200
    temperature: Optional[float] = 0.7

def format_reset_time(seconds: float) -> str:
    """Formats seconds into real Groq header format (e.g. '1m26.4s' or '510ms')."""
    if seconds < 1.0:
        return f"{int(seconds * 1000)}ms"
    mins = int(seconds // 60)
    rem_secs = seconds % 60
    if mins > 0:
        return f"{mins}m{rem_secs:.1f}s"
    return f"{rem_secs:.1f}s"

def get_rate_limit_headers() -> Dict[str, str]:
    state.rpd_bucket.update()
    state.tpm_bucket.update()
    
    rpd_limit = int(state.params.get("limits", {}).get("rpd", 1000))
    tpm_limit = int(state.params.get("limits", {}).get("tpm", 8000))
    
    remaining_reqs = max(0, int(state.rpd_bucket.current))
    remaining_tokens = max(0, int(state.tpm_bucket.current))
    
    deficit_reqs = max(0, rpd_limit - remaining_reqs)
    rpd_interval = state.params.get("limits", {}).get("rpd_refill_interval_seconds", 86.4)
    reset_reqs_secs = deficit_reqs * rpd_interval
    
    deficit_tokens = max(0, tpm_limit - remaining_tokens)
    reset_tokens_secs = (deficit_tokens / (tpm_limit / 60.0)) if tpm_limit > 0 else 0.5

    return {
        "x-ratelimit-limit-requests": str(rpd_limit),
        "x-ratelimit-remaining-requests": str(remaining_reqs),
        "x-ratelimit-reset-requests": format_reset_time(reset_reqs_secs),
        "x-ratelimit-limit-tokens": str(tpm_limit),
        "x-ratelimit-remaining-tokens": str(remaining_tokens),
        "x-ratelimit-reset-tokens": format_reset_time(reset_tokens_secs),
    }

def estimate_prompt_tokens(messages: List[ChatMessage]) -> int:
    text = " ".join(m.content for m in messages)
    # Roughly 1 token per 4 chars + base overhead
    return max(10, len(text.split()) + len(text) // 10)

def generate_mock_answer(messages: List[ChatMessage], max_tokens: int) -> str:
    user_msg = " ".join(m.content for m in messages if m.role == "user").lower()
    # If formatted as a pipeline prompt, inspect the Question section
    if "question:" in user_msg:
        q_part = user_msg.split("question:")[-1].split("cited answer:")[0].strip()
    else:
        q_part = user_msg
    
    # Grounded mock responses for library topics
    if "scaled dot-product" in q_part or "q01" in q_part:
        return 'Attention(Q, K, V) = softmax(QK^T / sqrt(d_k))V. Scaling prevents dot products from growing large [attention_v7, page 4].'
    elif "feed-forward" in q_part or "q04" in q_part:
        return 'The FFN applies "two linear transformations" with ReLU, d_model=512, d_ff=2048 [attention_v7, page 5].'
    elif "helium" in q_part or "q07" in q_part:
        return 'Helium is a "7-billion" parameter text LLM pretrained on "2.1 trillion" tokens of public English data [moshi_v1, page 3].'
    elif "routing module" in q_part or "q12" in q_part:
        return 'The routing module calculates "cosine similarity" and sets boundary threshold at 0.5 [dynchunk_v2, page 6].'
    elif "dna" in q_part or "q16" in q_part:
        return 'H-Net achieves "3.6" times greater "data efficiency" on DNA language modeling [dynchunk_v2, page 17].'
    elif "steady stand-in load" in q_part or "q19" in q_part:
        return 'The library does not contain information to answer this question as it pertains to internal benchmark experiments.'
    elif "external human-annotated" in q_part or "q20" in q_part:
        return 'None. H-Net learns chunk boundaries "without heuristics" and "without external supervision" [dynchunk_v2, page 1].'
    else:
        # Default answer
        base = 'The Transformer architecture relies entirely on self-attention mechanisms [attention_v7, page 1].'
        words = base.split()
        return " ".join(words[:max_tokens])

@app.post("/v1/chat/completions")
async def chat_completions(req: ChatCompletionRequest, request: Request):
    headers = get_rate_limit_headers()
    limits = state.params.get("limits", {})
    latency = state.params.get("latency", {})
    failures = state.params.get("failures", {})

    prompt_toks = estimate_prompt_tokens(req.messages)
    max_out = req.max_tokens or 150

    # 1. In-flight check
    max_in_flight = latency.get("max_in_flight", 16)
    if state.in_flight >= max_in_flight:
        state.rate_limited_count += 1
        headers["retry-after"] = "1.0"
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={"error": {"message": f"Server busy: in-flight limit ({max_in_flight}) reached", "type": "concurrency", "code": "rate_limit_exceeded"}},
            headers=headers
        )

    # 2. Rate limit checks: RPM, RPD, TPM, TPD
    if not state.rpm_bucket.try_consume(1.0):
        state.rate_limited_count += 1
        wait_s = math.ceil(state.rpm_bucket.time_until(1.0))
        headers["retry-after"] = str(max(1, wait_s))
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={"error": {"message": "Rate limit reached for requests per minute (RPM)", "type": "requests", "code": "rate_limit_exceeded"}},
            headers=headers
        )

    if not state.rpd_bucket.try_consume(1.0):
        state.rate_limited_count += 1
        wait_s = math.ceil(state.rpd_bucket.time_until(1.0))
        headers["retry-after"] = str(max(1, wait_s))
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={"error": {"message": "Rate limit reached for requests per day (RPD)", "type": "requests", "code": "rate_limit_exceeded"}},
            headers=headers
        )

    # Note: Check upfront reservation against TPM
    if not state.tpm_bucket.try_consume(prompt_toks):
        state.rate_limited_count += 1
        wait_s = max(1.0, state.tpm_bucket.time_until(prompt_toks))
        headers["retry-after"] = str(math.ceil(wait_s))
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={"error": {"message": "Rate limit reached for model on tokens per minute (TPM)", "type": "tokens", "code": "rate_limit_exceeded"}},
            headers=headers
        )

    if not state.tpd_bucket.try_consume(prompt_toks):
        state.rate_limited_count += 1
        wait_s = max(1.0, state.tpd_bucket.time_until(prompt_toks))
        headers["retry-after"] = str(math.ceil(wait_s))
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={"error": {"message": "Rate limit reached for model on tokens per day (TPD)", "type": "tokens", "code": "rate_limit_exceeded"}},
            headers=headers
        )

    # 3. Simulate failure injections
    if state.rng.random() < failures.get("error_rate", 0.0):
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": {"message": "Internal Groq simulation error", "type": "server_error"}},
            headers=headers
        )

    if state.rng.random() < failures.get("timeout_rate", 0.0):
        timeout_s = failures.get("timeout_delay_ms", 35000) / 1000.0
        await asyncio.sleep(timeout_s)
        return JSONResponse(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            content={"error": {"message": "Gateway Timeout", "type": "timeout"}},
            headers=headers
        )

    # 4. Latency calculation
    state.in_flight += 1
    state.total_requests += 1

    async def stream_generator():
        try:
            # Slow success mode check
            if state.rng.random() < failures.get("slow_success_rate", 0.0):
                slow_s = failures.get("slow_success_delay_ms", 12000) / 1000.0
                await asyncio.sleep(slow_s)

            # Queue time simulation
            q_min = latency.get("queue_time_ms_min", 49) / 1000.0
            q_max = latency.get("queue_time_ms_max", 251) / 1000.0
            simulated_queue_s = state.rng.uniform(q_min, q_max)
            await asyncio.sleep(simulated_queue_s)

            # TTFT calculation
            warm_mean = latency.get("warm_ttft_ms_mean", 380) / 1000.0
            warm_std = latency.get("warm_ttft_ms_std", 80) / 1000.0
            slowdown = 1.0 + (state.in_flight * latency.get("concurrency_slowdown_factor", 0.12))
            ttft_s = max(0.05, state.rng.gauss(warm_mean, warm_std)) * slowdown
            await asyncio.sleep(ttft_s)

            # Generate response content
            answer_text = generate_mock_answer(req.messages, max_out)
            words = answer_text.split()
            decode_rate = max(50.0, latency.get("decode_tokens_per_sec", 500) / slowdown)
            
            completion_id = f"chatcmpl-{uuid.uuid4()}"
            created_ts = int(time.time())
            
            # Stream words with decode pacing
            tokens_out_count = 0
            for w in words:
                chunk_delta = {"role": "assistant", "content": w + " "}
                chunk_payload = {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": created_ts,
                    "model": req.model,
                    "choices": [{"index": 0, "delta": chunk_delta, "finish_reason": None}]
                }
                tokens_out_count += 1
                yield f"data: {json.dumps(chunk_payload)}\n\n"
                # Paced delay per token
                await asyncio.sleep(1.0 / decode_rate)

            # Final closing chunk with usage
            state.total_tokens_in += prompt_toks
            state.total_tokens_out += tokens_out_count
            state.tpm_bucket.try_consume(tokens_out_count)
            state.tpd_bucket.try_consume(tokens_out_count)

            final_payload = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created_ts,
                "model": req.model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "x_groq": {
                    "id": f"req_{uuid.uuid4().hex[:24]}",
                    "usage": {
                        "queue_time": simulated_queue_s,
                        "prompt_tokens": prompt_toks,
                        "completion_tokens": tokens_out_count,
                        "total_tokens": prompt_toks + tokens_out_count
                    }
                }
            }
            yield f"data: {json.dumps(final_payload)}\n\n"
            yield "data: [DONE]\n\n"
        finally:
            state.in_flight = max(0, state.in_flight - 1)

    return StreamingResponse(
        stream_generator(),
        media_type="text/event-stream",
        headers=headers
    )

# --- Admin Endpoints ---

@app.get("/admin/params")
def get_params():
    return state.params

@app.post("/admin/params")
def update_params(new_params: dict):
    state.update_params(new_params)
    return {"status": "ok", "params": state.params}

@app.post("/admin/reset")
def reset_service():
    state.reset_buckets()
    state.in_flight = 0
    state.total_requests = 0
    state.total_tokens_in = 0
    state.total_tokens_out = 0
    state.rate_limited_count = 0
    return {"status": "reset", "buckets": "full"}

@app.get("/admin/metrics")
def get_metrics():
    state.rpd_bucket.update()
    state.tpm_bucket.update()
    return {
        "in_flight": state.in_flight,
        "total_requests": state.total_requests,
        "total_tokens_in": state.total_tokens_in,
        "total_tokens_out": state.total_tokens_out,
        "rate_limited_count": state.rate_limited_count,
        "remaining_tpm": state.tpm_bucket.current,
        "remaining_rpm": state.rpm_bucket.current,
        "remaining_rpd": state.rpd_bucket.current,
        "remaining_tpd": state.tpd_bucket.current,
    }
