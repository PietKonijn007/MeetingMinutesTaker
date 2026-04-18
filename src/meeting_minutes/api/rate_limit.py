"""In-memory sliding-window rate limiter for LLM-backed endpoints (H-4).

Prevents accidental or malicious quota drain on LLM providers (Anthropic,
OpenAI, OpenRouter). Keyed by client IP; thread-safe.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import HTTPException, Request


class RateLimiter:
    """Sliding-window rate limiter, keyed by an opaque string (e.g. client IP)."""

    def __init__(self, max_calls: int, window_seconds: float) -> None:
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self._calls: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def check(self, key: str) -> None:
        """Record a call; raise HTTPException(429) if the limit is exceeded."""
        now = time.time()
        cutoff = now - self.window_seconds
        with self._lock:
            calls = self._calls[key]
            while calls and calls[0] < cutoff:
                calls.popleft()
            if len(calls) >= self.max_calls:
                retry_after = int(calls[0] + self.window_seconds - now) + 1
                raise HTTPException(
                    status_code=429,
                    detail=(
                        f"Rate limit exceeded: {self.max_calls} LLM requests "
                        f"per {int(self.window_seconds)}s"
                    ),
                    headers={"Retry-After": str(max(retry_after, 1))},
                )
            calls.append(now)


# 10 LLM-backed calls per minute per client IP — generous for human use,
# prevents runaway scripts from burning through provider quota.
llm_limiter = RateLimiter(max_calls=10, window_seconds=60)


def check_llm_limit(request: Request) -> None:
    """FastAPI dependency — rate-limit LLM-backed endpoints by client IP."""
    client_ip = request.client.host if request.client else "unknown"
    llm_limiter.check(client_ip)
