"""Secret management — read / write / delete env-var values in ``.env``.

The recording pipeline reads API keys from environment variables (set by
``meeting_minutes.env.load_dotenv()`` at startup). To let users manage
those keys from the web UI without editing files on disk, this module
exposes a tiny GET/PUT/DELETE API backed by the gitignored ``.env``
file at the project root.

Security posture:

* The file is gitignored — ``echo .env >> .gitignore`` lives in the repo
  already, so values never end up in source control.
* GET never returns the value itself, only ``{is_set: bool}`` plus the
  prefix of long keys for visual confirmation. Once written, a key
  cannot be read back through the API.
* Only env-var names matching a strict identifier pattern are accepted,
  so a malicious caller can't write arbitrary content (``foo\nbar=...``).
* Changes apply on the next process start. We can't safely reload an
  already-loaded API key into running pyannote / openai clients.
"""

from __future__ import annotations

import re
from pathlib import Path

# A valid POSIX env-var name: letters/digits/underscore, must start with
# letter or underscore. We additionally require the name to be uppercase
# to match the project's existing convention and avoid accidental
# overlap with shell metavariables.
_NAME_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")


def is_valid_secret_name(name: str) -> bool:
    return bool(_NAME_PATTERN.match(name))


def _project_env_path() -> Path:
    """Return the path to the project's ``.env`` file.

    Mirrors :func:`meeting_minutes.env.load_dotenv` — first match wins:
    ``$repo/.env`` then ``~/.meeting-minutes/.env``. We always write back
    to whichever file we read from, falling through to the repo root when
    neither exists yet.
    """
    project_root = Path(__file__).resolve().parent.parent.parent.parent
    candidates = [
        project_root / ".env",
        Path.home() / ".meeting-minutes" / ".env",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def _read_dotenv() -> tuple[Path, list[str]]:
    """Return the path and the current line list (preserving order/comments)."""
    path = _project_env_path()
    if path.is_file():
        return path, path.read_text(encoding="utf-8").splitlines()
    return path, []


def _split_kv(line: str) -> tuple[str | None, str | None]:
    """Return (name, value) for an assignment line, else (None, None)."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None, None
    key, _, value = stripped.partition("=")
    key = key.strip()
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        value = value[1:-1]
    return key, value


def get_secret_status(name: str) -> dict:
    """Return whether the secret is set, plus a sanitized preview.

    Returns ``{is_set: bool, preview: str | None}``. The preview shows the
    first 4 chars and length so the user can confirm the right key without
    revealing it; for short keys (< 8 chars) we omit the preview entirely.
    """
    if not is_valid_secret_name(name):
        raise ValueError(f"Invalid env var name: {name!r}")
    _, lines = _read_dotenv()
    for line in lines:
        key, value = _split_kv(line)
        if key == name and value:
            preview = None
            if len(value) >= 8:
                preview = f"{value[:4]}…{value[-2:]} ({len(value)} chars)"
            return {"is_set": True, "preview": preview}
    return {"is_set": False, "preview": None}


def set_secret(name: str, value: str) -> None:
    """Insert or update a secret in ``.env``.

    Preserves comments and other entries. If the key already exists, its
    line is rewritten in place; otherwise a new line is appended.
    """
    if not is_valid_secret_name(name):
        raise ValueError(f"Invalid env var name: {name!r}")
    if "\n" in value or "\r" in value:
        raise ValueError("Secret values must not contain newlines.")
    if not value:
        raise ValueError("Secret value cannot be empty. Use DELETE to clear.")

    path, lines = _read_dotenv()
    new_line = f'{name}="{value}"'
    rewrote = False
    for i, line in enumerate(lines):
        key, _ = _split_kv(line)
        if key == name:
            lines[i] = new_line
            rewrote = True
            break
    if not rewrote:
        lines.append(new_line)

    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep a single trailing newline so editors don't whine.
    path.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
    # Restrict permissions — file may contain credentials.
    try:
        path.chmod(0o600)
    except OSError:
        pass  # best-effort on platforms without POSIX perms


def clear_secret(name: str) -> bool:
    """Remove a secret from ``.env``. Returns True if a row was removed."""
    if not is_valid_secret_name(name):
        raise ValueError(f"Invalid env var name: {name!r}")
    path, lines = _read_dotenv()
    new_lines = []
    removed = False
    for line in lines:
        key, _ = _split_kv(line)
        if key == name:
            removed = True
            continue
        new_lines.append(line)
    if removed:
        path.write_text("\n".join(new_lines).rstrip("\n") + "\n" if new_lines else "", encoding="utf-8")
    return removed
