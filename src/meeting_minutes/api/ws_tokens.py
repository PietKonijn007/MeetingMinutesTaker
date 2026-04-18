"""One-time tokens for WebSocket handshake authentication (H-1).

WebSockets do not honor CORS the way REST endpoints do, so a malicious site
visited in the same browser could otherwise open a connection to our local
WebSocket endpoints. Requiring a token obtained via a CORS-protected REST
call prevents that: the REST call would be blocked by the browser's CORS
policy for cross-origin requests.
"""

from __future__ import annotations

import secrets
import time
from threading import Lock

TTL_SECONDS = 60

_tokens: dict[str, float] = {}  # token -> expires_at (unix seconds)
_lock = Lock()


def issue_token() -> tuple[str, int]:
    """Mint a new one-time WebSocket token. Returns (token, ttl_seconds)."""
    token = secrets.token_urlsafe(32)
    expires_at = time.time() + TTL_SECONDS
    with _lock:
        _reap_expired_locked()
        _tokens[token] = expires_at
    return token, TTL_SECONDS


def consume_token(token: str | None) -> bool:
    """Validate and consume a token (single-use). Returns True on success."""
    if not token:
        return False
    with _lock:
        _reap_expired_locked()
        expires_at = _tokens.pop(token, None)
    return expires_at is not None and expires_at > time.time()


def _reap_expired_locked() -> None:
    now = time.time()
    expired = [t for t, exp in _tokens.items() if exp <= now]
    for t in expired:
        _tokens.pop(t, None)
