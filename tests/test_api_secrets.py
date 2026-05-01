"""Tests for the secret-management module + API endpoints.

The module reads/writes to a ``.env`` file at the project root; tests
swap that location to a tmp_path so they never touch the real file.
"""

from __future__ import annotations

import pytest

from meeting_minutes.api import secrets as secrets_mod


@pytest.fixture
def tmp_dotenv(monkeypatch, tmp_path):
    """Point the secrets module at a throwaway ``.env`` for the test."""
    env_path = tmp_path / ".env"
    monkeypatch.setattr(secrets_mod, "_project_env_path", lambda: env_path)
    return env_path


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name,expected", [
    ("PYANNOTEAI_API_KEY", True),
    ("HF_TOKEN", True),
    ("MY_KEY_42", True),
    ("_PRIVATE", True),
    ("anthropic_api_key", False),  # lowercase rejected
    ("KEY-WITH-DASH", False),
    ("KEY WITH SPACE", False),
    ("", False),
    ("9_STARTS_WITH_DIGIT", False),
])
def test_is_valid_secret_name(name, expected):
    assert secrets_mod.is_valid_secret_name(name) is expected


# ---------------------------------------------------------------------------
# get_secret_status
# ---------------------------------------------------------------------------


def test_get_secret_status_unset(tmp_dotenv):
    status = secrets_mod.get_secret_status("PYANNOTEAI_API_KEY")
    assert status == {"is_set": False, "preview": None}


def test_get_secret_status_invalid_name(tmp_dotenv):
    with pytest.raises(ValueError):
        secrets_mod.get_secret_status("invalid lowercase")


def test_set_then_get_secret(tmp_dotenv):
    secrets_mod.set_secret("PYANNOTEAI_API_KEY", "sk-test-1234567890abcdef")
    status = secrets_mod.get_secret_status("PYANNOTEAI_API_KEY")
    assert status["is_set"] is True
    # Preview should mask the middle but show prefix/length
    assert status["preview"].startswith("sk-t")
    assert "(24 chars)" in status["preview"]


def test_set_secret_short_key_omits_preview(tmp_dotenv):
    """Keys < 8 chars don't get a preview to avoid leaking the whole thing."""
    secrets_mod.set_secret("HF_TOKEN", "abc123")
    status = secrets_mod.get_secret_status("HF_TOKEN")
    assert status == {"is_set": True, "preview": None}


def test_set_secret_rewrites_existing(tmp_dotenv):
    secrets_mod.set_secret("PYANNOTEAI_API_KEY", "first-value-aaaaaaaa")
    secrets_mod.set_secret("PYANNOTEAI_API_KEY", "second-value-bbbbbbb")
    contents = tmp_dotenv.read_text()
    # Only one occurrence — no duplicates left in .env
    assert contents.count("PYANNOTEAI_API_KEY=") == 1
    assert "second-value-bbbbbbb" in contents


def test_set_secret_preserves_other_entries(tmp_dotenv):
    """Adding a new key must not clobber an existing unrelated entry."""
    tmp_dotenv.write_text('# comment\nHF_TOKEN="hf_existing"\n')
    secrets_mod.set_secret("PYANNOTEAI_API_KEY", "pa-new-1234567890")
    contents = tmp_dotenv.read_text()
    assert 'HF_TOKEN="hf_existing"' in contents
    assert 'PYANNOTEAI_API_KEY="pa-new-1234567890"' in contents
    assert "# comment" in contents


def test_set_secret_rejects_newline(tmp_dotenv):
    with pytest.raises(ValueError):
        secrets_mod.set_secret("PYANNOTEAI_API_KEY", "a\nb")


def test_set_secret_rejects_empty(tmp_dotenv):
    with pytest.raises(ValueError):
        secrets_mod.set_secret("PYANNOTEAI_API_KEY", "")


def test_clear_secret(tmp_dotenv):
    secrets_mod.set_secret("PYANNOTEAI_API_KEY", "secretvalue123")
    assert secrets_mod.clear_secret("PYANNOTEAI_API_KEY") is True
    assert secrets_mod.get_secret_status("PYANNOTEAI_API_KEY")["is_set"] is False
    # Clearing again is a no-op (returns False, doesn't raise)
    assert secrets_mod.clear_secret("PYANNOTEAI_API_KEY") is False


def test_set_secret_perms(tmp_dotenv):
    """The .env file should not be world-readable after a write."""
    import stat
    secrets_mod.set_secret("PYANNOTEAI_API_KEY", "shouldbelocked123")
    mode = tmp_dotenv.stat().st_mode
    # Other-readable bit must not be set
    assert not (mode & stat.S_IROTH), oct(mode)
