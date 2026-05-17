"""In-memory sliding-window rate limiters (H-4).

Two layers:
1. ``RateLimitMiddleware`` — global per-IP cap on all ``/api/*`` requests,
   preventing brute-force or DoS abuse. Disabled (no-op) when
   *requests_per_minute* is 0.
2. ``RateLimiter`` / ``check_llm_limit`` — stricter per-IP cap on
   LLM-backed endpoints, preventing accidental quota drain on cloud
   providers.  Used as a FastAPI dependency.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


# ---------------------------------------------------------------------------
# Global API rate-limit middleware
# ---------------------------------------------------------------------------

class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window rate limiter keyed by client IP.

    Applies to all ``/api/`` requests.  Set *requests_per_minute* to 0
    to disable (no-op, the default for backward compatibility).
    """

    def __init__(self, app, requests_per_minute: int = 0):
        super().__init__(app)
        self._rpm = requests_per_minute
        self._lock = Lock()
        self._buckets: dict[str, deque[float]] = defaultdict(deque)

    async def dispatch(self, request, call_next):
        if not self._rpm:
            return await call_next(request)

        if not request.url.path.startswith("/api/"):
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        now = time.monotonic()
        window = 60.0

        with self._lock:
            timestamps = self._buckets[client_ip]
            while timestamps and timestamps[0] < now - window:
                timestamps.popleft()
            if len(timestamps) >= self._rpm:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Rate limit exceeded. Try again later."},
                    headers={"Retry-After": "60"},
                )
            timestamps.append(now)

        return await call_next(request)


# ---------------------------------------------------------------------------
# Per-endpoint LLM rate limiter (FastAPI dependency)
# ---------------------------------------------------------------------------

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
