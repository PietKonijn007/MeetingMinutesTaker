"""Load environment variables from .env files, falling back to the real environment.

Avoids a dependency on python-dotenv by implementing a minimal parser.
"""

from __future__ import annotations

import os
from pathlib import Path


def _parse_dotenv(path: Path) -> dict[str, str]:
    """Parse a simple .env file into a dict. Supports KEY=VALUE and KEY="VALUE"."""
    result: dict[str, str] = {}
    if not path.is_file():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Strip surrounding quotes
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        result[key] = value
    return result


def load_dotenv() -> None:
    """Load .env from the project root into os.environ (does NOT overwrite existing vars)."""
    # Search upward from this file's location to find .env
    candidates = [
        Path(__file__).resolve().parent.parent.parent / ".env",  # project root
        Path.cwd() / ".env",
        Path.home() / ".meeting-minutes" / ".env",
    ]
    for candidate in candidates:
        env_vars = _parse_dotenv(candidate)
        if env_vars:
            for key, value in env_vars.items():
                if key not in os.environ:
                    os.environ[key] = value
            return  # stop after the first .env file found
