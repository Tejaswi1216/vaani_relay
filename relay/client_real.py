import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple
import httpx

from relay.config import GROQ_API_BASE, MODEL_NAME, get_groq_api_key
from relay.metrics import AttemptSpan
from relay.policy import RetryPolicy

class RealGroqClient:
    """
    Asynchronous client for Groq API using one shared httpx.AsyncClient.
    Enforces R1 (key loaded only via relay.config) and records detailed per-attempt spans.
    """

    def __init__(self, base_url: str = GROQ_API_BASE, client: Optional[httpx.AsyncClient] = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = get_groq_api_key()
        self._client = client or httpx.AsyncClient(timeout=60.0)
        self._owns_client = client is None

    async def close(self):
        if self._owns_client and not self._client.is_closed:
            await self._client.aclose()

    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int = 100,
        stream: bool = True,
        policy: Optional[RetryPolicy] = None
    ) -> Tuple[Optional[str], Optional[float], int, int, List[AttemptSpan], str, Optional[Dict[str, Any]]]:
        """
        Executes chat completion against real Groq endpoint.
        Returns:
            (answer_text, first_token_ms, tokens_in, tokens_out, attempts, status, last_error_body)
        """
        policy = policy or RetryPolicy()
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        body = {
            "model": MODEL_NAME,
            "messages": messages,
            "stream": stream,
            "max_tokens": max_tokens
        }

        attempts: List[AttemptSpan] = []
        overall_start = time.perf_counter()
        last_body = None

        for attempt_idx in range(policy.max_retries + 1):
            send_ts = datetime.now(timezone.utc).isoformat()
            span = AttemptSpan(attempt_idx=attempt_idx, send_ts=send_ts)
            attempts.append(span)

            elapsed_ms = (time.perf_counter() - overall_start) * 1000.0
            if elapsed_ms > policy.patience_total_ms:
                span.error = f"Deadline exceeded ({elapsed_ms:.0f}ms > {policy.patience_total_ms}ms)"
                span.end_ts = datetime.now(timezone.utc).isoformat()
                return None, None, 0, 0, attempts, "timeout", None

            t0 = time.perf_counter()
            first_byte_time = None
            content_chunks: List[str] = []
            tokens_in = 0
            tokens_out = 0

            try:
                if stream:
                    async with self._client.stream("POST", url, json=body, headers=headers) as resp:
                        span.status_code = resp.status_code
                        span.headers = {k.lower(): v for k, v in resp.headers.items()}

                        if "retry-after" in span.headers:
                            try:
                                span.retry_after = float(span.headers["retry-after"])
                            except ValueError:
                                span.retry_after = 1.0

                        if resp.status_code == 429:
                            raw_err = await resp.aread()
                            try:
                                last_body = json.loads(raw_err.decode("utf-8"))
                            except Exception:
                                last_body = {"raw": raw_err.decode("utf-8", errors="replace")}
                            span.end_ts = datetime.now(timezone.utc).isoformat()
                            
                            if attempt_idx < policy.max_retries:
                                sleep_s = policy.calculate_backoff(attempt_idx, span.retry_after)
                                span.backoff_sleep_ms = sleep_s * 1000.0
                                if (time.perf_counter() - overall_start + sleep_s) * 1000.0 > policy.patience_total_ms:
                                    return None, None, 0, 0, attempts, "timeout", last_body
                                await asyncio.sleep(sleep_s)
                                continue
                            else:
                                return None, None, 0, 0, attempts, "rate_limited", last_body

                        if resp.status_code >= 400:
                            raw_err = await resp.aread()
                            try:
                                last_body = json.loads(raw_err.decode("utf-8"))
                            except Exception:
                                last_body = {"raw": raw_err.decode("utf-8", errors="replace")}
                            span.end_ts = datetime.now(timezone.utc).isoformat()
                            span.error = f"HTTP {resp.status_code}"
                            return None, None, 0, 0, attempts, "error", last_body

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
                                
                                # Check Groq usage field
                                x_groq = payload.get("x_groq", {})
                                usage = x_groq.get("usage") or payload.get("usage")
                                if usage:
                                    tokens_in = usage.get("prompt_tokens", 0)
                                    tokens_out = usage.get("completion_tokens", 0)

                        span.end_ts = datetime.now(timezone.utc).isoformat()
                        full_answer = "".join(content_chunks).strip()
                        first_token_ms = ((first_byte_time - t0) * 1000.0) if first_byte_time else None
                        return full_answer, first_token_ms, tokens_in, tokens_out, attempts, "ok", None

                else:
                    resp = await self._client.post(url, json=body, headers=headers)
                    span.status_code = resp.status_code
                    span.headers = {k.lower(): v for k, v in resp.headers.items()}
                    span.end_ts = datetime.now(timezone.utc).isoformat()
                    
                    if resp.status_code == 429:
                        try:
                            last_body = resp.json()
                        except Exception:
                            last_body = {"raw": resp.text}
                        return None, None, 0, 0, attempts, "rate_limited", last_body

                    if resp.status_code >= 400:
                        try:
                            last_body = resp.json()
                        except Exception:
                            last_body = {"raw": resp.text}
                        return None, None, 0, 0, attempts, "error", last_body

                    data = resp.json()
                    answer = data["choices"][0]["message"]["content"]
                    usage = data.get("usage", {})
                    tokens_in = usage.get("prompt_tokens", 0)
                    tokens_out = usage.get("completion_tokens", 0)
                    total_time = (time.perf_counter() - t0) * 1000.0
                    return answer, total_time, tokens_in, tokens_out, attempts, "ok", None

            except (httpx.TimeoutException, httpx.NetworkError) as e:
                span.end_ts = datetime.now(timezone.utc).isoformat()
                span.error = str(e)
                if attempt_idx < policy.max_retries:
                    sleep_s = policy.calculate_backoff(attempt_idx)
                    span.backoff_sleep_ms = sleep_s * 1000.0
                    await asyncio.sleep(sleep_s)
                    continue
                else:
                    return None, None, 0, 0, attempts, "timeout", None

        return None, None, 0, 0, attempts, "error", last_body
