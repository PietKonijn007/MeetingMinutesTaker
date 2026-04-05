"""Security API routes."""

from __future__ import annotations

from fastapi import APIRouter

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
