import asyncio
import math
import random
import time
from typing import Optional, Tuple
from dataclasses import dataclass
from relay.config import PATIENCE_TTFT_MS, PATIENCE_TOTAL_MS

@dataclass
class RetryPolicy:
    max_retries: int = 3
    base_backoff_ms: float = 500.0
    max_backoff_ms: float = 5000.0
    patience_total_ms: float = PATIENCE_TOTAL_MS
    patience_ttft_ms: float = PATIENCE_TTFT_MS

    def calculate_backoff(self, attempt: int, retry_after_s: Optional[float] = None) -> float:
        """
        Calculates backoff delay in seconds using exponential backoff with full jitter,
        honouring retry-after from headers if present.
        """
        # Full jitter exponential backoff: Uniform(0, min(max_backoff, base * 2^attempt))
        exp_backoff = min(self.max_backoff_ms, self.base_backoff_ms * (2 ** attempt))
        jittered_ms = random.uniform(0, exp_backoff)
        jittered_s = jittered_ms / 1000.0

        if retry_after_s is not None and retry_after_s > 0:
            return max(retry_after_s, jittered_s)
        return jittered_s

class LocalTokenBucket:
    """Tracks local client-side token bucket to prevent sending over-quota requests."""
    def __init__(self, capacity: float, refill_rate_per_sec: float):
        self.capacity = capacity
        self.refill_rate = refill_rate_per_sec
        self.current = capacity
        self.last_update = time.monotonic()

    def update(self) -> float:
        now = time.monotonic()
        delta = now - self.last_update
        self.last_update = now
        self.current = min(self.capacity, self.current + delta * self.refill_rate)
        return self.current

    def try_reserve(self, amount: float) -> bool:
        self.update()
        if self.current >= amount:
            self.current -= amount
            return True
        return False

    def time_until_available(self, amount: float) -> float:
        self.update()
        if self.current >= amount:
            return 0.0
        deficit = amount - self.current
        return deficit / self.refill_rate if self.refill_rate > 0 else 60.0

class AdmissionController:
    """
    Shaping Decision 1: Token-Aware Dual-Leaky-Bucket Admission Control.
    Maintains bounded FIFO queue and rejects early if wait > PATIENCE_TTFT_MS or queue is full.
    """
    def __init__(
        self,
        tpm_capacity: float = 8000.0,
        tpd_capacity: float = 200000.0,
        max_queue_size: int = 25,
        token_reservation: float = 450.0
    ):
        self.tpm_bucket = LocalTokenBucket(capacity=tpm_capacity, refill_rate_per_sec=tpm_capacity / 60.0)
        self.tpd_bucket = LocalTokenBucket(capacity=tpd_capacity, refill_rate_per_sec=tpd_capacity / 86400.0)
        self.max_queue_size = max_queue_size
        self.token_reservation = token_reservation
        self.lock = asyncio.Lock()
        self.queue_length = 0

    async def acquire_admission(self) -> Tuple[bool, float, str]:
        """
        Attempts admission. Returns (admitted, wait_seconds, reason).
        If admitted is False, request should be shed immediately with status rate_limited.
        """
        async with self.lock:
            if self.queue_length >= self.max_queue_size:
                return False, 1.0, f"Admission queue full ({self.queue_length}/{self.max_queue_size})"

            tpm_wait = self.tpm_bucket.time_until_available(self.token_reservation)
            tpd_wait = self.tpd_bucket.time_until_available(self.token_reservation)
            max_wait_s = max(tpm_wait, tpd_wait)

            # Check if expected wait exceeds TTFT patience
            if max_wait_s * 1000.0 > PATIENCE_TTFT_MS:
                return False, max_wait_s, f"Estimated token refill wait ({max_wait_s:.1f}s) exceeds TTFT patience ({PATIENCE_TTFT_MS/1000}s)"

            self.queue_length += 1

        # Wait for bucket availability outside the lock
        t0 = time.monotonic()
        try:
            if max_wait_s > 0:
                await asyncio.sleep(max_wait_s)
            
            # Consume reservation
            async with self.lock:
                self.tpm_bucket.try_reserve(self.token_reservation)
                self.tpd_bucket.try_reserve(self.token_reservation)
            
            actual_wait_s = time.monotonic() - t0
            return True, actual_wait_s, "Admitted"
        finally:
            async with self.lock:
                self.queue_length = max(0, self.queue_length - 1)

    def record_actual_usage(self, actual_tokens: int):
        """Reconciles reservation with actual tokens used."""
        diff = actual_tokens - self.token_reservation
        if diff > 0:
            self.tpm_bucket.try_reserve(diff)
            self.tpd_bucket.try_reserve(diff)
