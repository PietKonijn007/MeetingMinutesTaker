"""API key authentication middleware (SEC-1).

When ``security.api_key`` is configured (via config YAML or ``MM_API_KEY``
env var), every ``/api/*`` request must include a matching ``X-Api-Key``
header. When the key is empty (the default), all requests pass through
so existing installations are unaffected.

Exemptions:
- ``GET /api/health`` — monitoring probes should work without credentials.
- Non-API paths (``/``, ``/_app/*``, static assets) are never gated.
- WebSocket upgrade requests are handled by ws_tokens.py and are exempt here.
"""

from __future__ import annotations

import hmac
import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

_EXEMPT_PREFIXES = (
    "/api/health",
)


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """Reject ``/api/*`` requests that lack a valid API key."""

    def __init__(self, app, api_key: str = ""):
        super().__init__(app)
        self._api_key = api_key

    async def dispatch(self, request: Request, call_next):
        if not self._api_key:
            return await call_next(request)

        path = request.url.path

        if not path.startswith("/api/"):
            return await call_next(request)

        for prefix in _EXEMPT_PREFIXES:
            if path.startswith(prefix):
                return await call_next(request)

        # WebSocket upgrades are authenticated via one-time tokens
        if request.headers.get("upgrade", "").lower() == "websocket":
            return await call_next(request)

        provided = request.headers.get("x-api-key", "")
        if not provided or not hmac.compare_digest(provided, self._api_key):
            logger.warning("Rejected unauthenticated request: %s %s", request.method, path)
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or missing API key"},
            )

        return await call_next(request)
