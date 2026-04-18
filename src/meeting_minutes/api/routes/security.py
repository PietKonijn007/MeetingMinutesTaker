"""Security API routes."""

from __future__ import annotations

from fastapi import APIRouter

from meeting_minutes.api.ws_tokens import issue_token

router = APIRouter(prefix="/api/security", tags=["security"])


@router.post("/generate-key")
async def generate_encryption_key():
    """Generate a new Fernet encryption key."""
    try:
        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode()
        return {"key": key}
    except ImportError:
        return {"key": "", "error": "cryptography package not installed"}


@router.post("/ws-token")
async def mint_ws_token():
    """Issue a short-lived one-time token for WebSocket handshake auth (H-1).

    The token must be passed as `?token=<value>` when opening a WebSocket
    connection. This REST endpoint is CORS-protected, so cross-origin pages
    cannot obtain tokens even though WebSockets themselves ignore CORS.
    """
    token, ttl = issue_token()
    return {"token": token, "expires_in": ttl}
