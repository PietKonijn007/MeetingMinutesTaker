"""Recording state file helpers.

The state file records the meeting_id of an in-progress recording so the
CLI ``mm record stop`` command can find it.  Previously stored in ``/tmp``
(world-readable); now lives under ``~/.meeting-minutes/`` with 0600 perms.
On read, the old ``/tmp`` location is checked as a fallback so upgrades
are seamless.
"""

from __future__ import annotations

import json
from pathlib import Path

_STATE_DIR = Path.home() / ".meeting-minutes"
_STATE_FILE = _STATE_DIR / "recording_state.json"
_LEGACY_STATE_FILE = Path("/tmp/mm_recording_state.json")


def write_recording_state(meeting_id: str) -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(json.dumps({"meeting_id": meeting_id}))
    try:
        _STATE_FILE.chmod(0o600)
    except OSError:
        pass


def read_recording_state() -> dict | None:
    for path in (_STATE_FILE, _LEGACY_STATE_FILE):
        if path.exists():
            try:
                return json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                continue
    return None


def clear_recording_state() -> None:
    for path in (_STATE_FILE, _LEGACY_STATE_FILE):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
