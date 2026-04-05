"""Optional at-rest encryption for meeting data files."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def get_fernet(key: str):
    """Get a Fernet cipher from a key string."""
    try:
        from cryptography.fernet import Fernet

        return Fernet(key.encode() if isinstance(key, str) else key)
    except ImportError:
        raise RuntimeError(
            "cryptography package not installed. Run: pip install cryptography"
        )


def encrypt_file(file_path: Path, key: str) -> None:
    """Encrypt a file in-place using Fernet."""
    if not key:
        return
    f = get_fernet(key)
    data = file_path.read_bytes()
    encrypted = f.encrypt(data)
    file_path.write_bytes(encrypted)
    logger.debug("Encrypted: %s", file_path.name)


def decrypt_file(file_path: Path, key: str) -> bytes:
    """Decrypt a file and return the decrypted bytes."""
    if not key:
        return file_path.read_bytes()
    f = get_fernet(key)
    encrypted = file_path.read_bytes()
    try:
        return f.decrypt(encrypted)
    except Exception:
        # File might not be encrypted (pre-encryption data)
        logger.debug(
            "File not encrypted or wrong key, returning raw: %s", file_path.name
        )
        return encrypted


def decrypt_file_text(file_path: Path, key: str, encoding: str = "utf-8") -> str:
    """Decrypt a file and return as text."""
    return decrypt_file(file_path, key).decode(encoding)


def is_encrypted(file_path: Path) -> bool:
    """Check if a file appears to be Fernet-encrypted (starts with gAAAAA)."""
    try:
        header = file_path.read_bytes()[:6]
        return header.startswith(b"gAAAAA")
    except Exception:
        return False
