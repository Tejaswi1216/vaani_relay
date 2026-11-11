import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple, AsyncGenerator
import httpx

from relay.config import STANDIN_API_BASE, MODEL_NAME
from relay.metrics import AttemptSpan
from relay.policy import RetryPolicy

class StandinClient:
    """Async client communicating with the stand-in model service using one shared httpx.AsyncClient."""

    def __init__(self, base_url: str = STANDIN_API_BASE, client: Optional[httpx.AsyncClient] = None):
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=45.0)
        self._owns_client = client is None

    async def close(self):
        if self._owns_client and not self._client.is_closed:
            await self._client.aclose()

    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int = 220,
        policy: Optional[RetryPolicy] = None
    ) -> Tuple[Optional[str], Optional[float], int, int, List[AttemptSpan], str]:
        """
        Executes streaming chat completion with retry policy.
        Returns:
            (answer_text, first_token_latency_ms, tokens_in, tokens_out, attempts, final_status)
        """
        policy = policy or RetryPolicy()
        url = f"{self.base_url}/chat/completions"
        body = {
            "model": MODEL_NAME,
            "messages": messages,
            "stream": True,
            "max_tokens": max_tokens
        }

        attempts: List[AttemptSpan] = []
        overall_start = time.perf_counter()

        for attempt_idx in range(policy.max_retries + 1):
            send_ts = datetime.now(timezone.utc).isoformat()
            span = AttemptSpan(attempt_idx=attempt_idx, send_ts=send_ts)
            attempts.append(span)

            # Check overall deadline
            elapsed_ms = (time.perf_counter() - overall_start) * 1000.0
            if elapsed_ms > policy.patience_total_ms:
                span.error = f"Deadline exceeded ({elapsed_ms:.0f}ms > {policy.patience_total_ms}ms)"
                span.end_ts = datetime.now(timezone.utc).isoformat()
                return None, None, 0, 0, attempts, "timeout"

            t0 = time.perf_counter()
            first_byte_time = None
            content_chunks: List[str] = []
            tokens_in = 0
            tokens_out = 0

            try:
                async with self._client.stream("POST", url, json=body) as resp:
                    span.status_code = resp.status_code
                    span.headers = {k.lower(): v for k, v in resp.headers.items()}
                    
                    if "retry-after" in span.headers:
                        try:
                            span.retry_after = float(span.headers["retry-after"])
                        except ValueError:
                            span.retry_after = 1.0

                    if resp.status_code == 429:
                        span.end_ts = datetime.now(timezone.utc).isoformat()
                        # Rate limited: check if retry is feasible before deadline
                        if attempt_idx < policy.max_retries:
                            sleep_s = policy.calculate_backoff(attempt_idx, span.retry_after)
                            span.backoff_sleep_ms = sleep_s * 1000.0
                            if (time.perf_counter() - overall_start + sleep_s) * 1000.0 > policy.patience_total_ms:
                                return None, None, 0, 0, attempts, "timeout"
                            await asyncio.sleep(sleep_s)
                            continue
                        else:
                            return None, None, 0, 0, attempts, "rate_limited"

                    if resp.status_code in [408, 504]:
                        span.end_ts = datetime.now(timezone.utc).isoformat()
                        span.error = f"HTTP {resp.status_code} Timeout"
                        return None, None, 0, 0, attempts, "timeout"

                    if resp.status_code >= 500:
                        span.end_ts = datetime.now(timezone.utc).isoformat()
                        span.error = f"HTTP {resp.status_code}"
                        if attempt_idx < policy.max_retries:
                            sleep_s = policy.calculate_backoff(attempt_idx, span.retry_after)
                            span.backoff_sleep_ms = sleep_s * 1000.0
                            await asyncio.sleep(sleep_s)
                            continue
                        else:
                            return None, None, 0, 0, attempts, "error"

                    # Read SSE stream
                    async for line in resp.aiter_lines():
                        if not line:
                            continue
                        if line.startswith("data: ") and line != "data: [DONE]":
                            payload = json.loads(line[6:])
                            ch = payload.get("choices") or []
                            if ch:
                                delta = ch[0].get("delta", {})
                                content = delta.get("content")
                                if content:
                                    if first_byte_time is None:
                                        first_byte_time = time.perf_counter()
                                        span.first_byte_ts = datetime.now(timezone.utc).isoformat()
                                    content_chunks.append(content)
                            
                            # Usage reporting
                            x_groq = payload.get("x_groq", {})
                            usage = x_groq.get("usage") or payload.get("usage")
                            if usage:
                                tokens_in = usage.get("prompt_tokens", 0)
                                tokens_out = usage.get("completion_tokens", 0)

                    span.end_ts = datetime.now(timezone.utc).isoformat()
                    full_answer = "".join(content_chunks).strip()
                    first_token_ms = ((first_byte_time - t0) * 1000.0) if first_byte_time else None
                    
                    # If usage wasn't reported in stream, estimate fallback
                    if tokens_in == 0:
                        tokens_in = len(" ".join(m["content"] for m in messages).split())
                        tokens_out = len(full_answer.split())

                    return full_answer, first_token_ms, tokens_in, tokens_out, attempts, "ok"

            except (httpx.TimeoutException, httpx.NetworkError) as e:
                span.end_ts = datetime.now(timezone.utc).isoformat()
                span.error = str(e)
                if attempt_idx < policy.max_retries:
                    sleep_s = policy.calculate_backoff(attempt_idx)
                    span.backoff_sleep_ms = sleep_s * 1000.0
                    await asyncio.sleep(sleep_s)
                    continue
                else:
                    return None, None, 0, 0, attempts, "timeout"

        return None, None, 0, 0, attempts, "error"
