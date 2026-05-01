"""Tests for the pluggable diarization-backend architecture.

These tests don't load any real models — the goal is to verify the
selector wiring, configuration plumbing, and the cloud backend's response
parsing. The local PyTorch path is exercised by the pre-existing
``test_diarize.py`` suite.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from meeting_minutes.config import DiarizationConfig
from meeting_minutes.system1.diarization_backends import select_backend
from meeting_minutes.system1.diarization_backends.base import (
    DiarizationBackend,
    normalize_label,
)


# ---------------------------------------------------------------------------
# selector
# ---------------------------------------------------------------------------


def test_selector_pyannote_local():
    cfg = DiarizationConfig(enabled=False, engine="pyannote")
    backend = select_backend(cfg)
    from meeting_minutes.system1.diarization_backends.pyannote_local import (
        PyannoteLocalBackend,
    )
    assert isinstance(backend, PyannoteLocalBackend)


def test_selector_pyannote_ai_alias():
    """``pyannote-ai`` and the alias ``pyannoteai`` both pick the cloud backend."""
    from meeting_minutes.system1.diarization_backends.pyannote_ai import (
        PyannoteAIBackend,
    )
    for engine in ("pyannote-ai", "pyannoteai", "PYANNOTE-AI"):
        cfg = DiarizationConfig(enabled=False, engine=engine)
        backend = select_backend(cfg)
        assert isinstance(backend, PyannoteAIBackend), engine


def test_selector_pyannote_mlx():
    cfg = DiarizationConfig(enabled=False, engine="pyannote-mlx")
    backend = select_backend(cfg)
    from meeting_minutes.system1.diarization_backends.pyannote_mlx import (
        PyannoteMLXBackend,
    )
    assert isinstance(backend, PyannoteMLXBackend)


def test_selector_unknown_engine_raises():
    cfg = DiarizationConfig(enabled=False, engine="bogus")
    with pytest.raises(ValueError, match="Unknown diarization engine"):
        select_backend(cfg)


# ---------------------------------------------------------------------------
# normalize_label
# ---------------------------------------------------------------------------


def test_normalize_label_canonical():
    assert normalize_label("SPEAKER_00") == "SPEAKER_00"
    assert normalize_label("SPEAKER_42") == "SPEAKER_42"


def test_normalize_label_short_form():
    """pyannoteAI returns SPEAKER_0 / SPEAKER_1 — must coerce to two-digit."""
    assert normalize_label("SPEAKER_0") == "SPEAKER_00"
    assert normalize_label("SPEAKER_5") == "SPEAKER_05"


def test_normalize_label_lowercase():
    assert normalize_label("speaker_3") == "SPEAKER_03"


def test_normalize_label_no_digits_falls_back():
    assert normalize_label("Alice") == "SPEAKER_00"


# ---------------------------------------------------------------------------
# pyannote-ai cloud backend
# ---------------------------------------------------------------------------


def test_pyannote_ai_returns_empty_when_disabled():
    cfg = DiarizationConfig(enabled=False, engine="pyannote-ai")
    from meeting_minutes.system1.diarization_backends.pyannote_ai import (
        PyannoteAIBackend,
    )
    backend = PyannoteAIBackend(cfg)
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".flac") as f:
        result = backend.diarize(Path(f.name))
    assert result.num_speakers == 0
    assert result.segments == []


def test_pyannote_ai_returns_empty_when_api_key_missing(monkeypatch):
    monkeypatch.delenv("PYANNOTEAI_API_KEY", raising=False)
    cfg = DiarizationConfig(enabled=True, engine="pyannote-ai")
    from meeting_minutes.system1.diarization_backends.pyannote_ai import (
        PyannoteAIBackend,
    )
    backend = PyannoteAIBackend(cfg)
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".flac") as f:
        result = backend.diarize(Path(f.name))
    # Missing API key shouldn't crash the recording pipeline — just empty.
    assert result.num_speakers == 0


def test_pyannote_ai_parses_response_with_diarization_key(monkeypatch):
    """The SDK's ``output['diarization']`` shape produces correct segments."""
    monkeypatch.setenv("PYANNOTEAI_API_KEY", "fake-key")

    fake_client = MagicMock()
    fake_client.upload.return_value = "media://fake"
    fake_client.diarize.return_value = "job-1"
    fake_client.retrieve.return_value = {
        "output": {
            "diarization": [
                {"start": 0.0, "end": 5.0, "speaker": "SPEAKER_0"},
                {"start": 5.0, "end": 10.0, "speaker": "SPEAKER_1"},
                {"start": 10.0, "end": 15.0, "speaker": "SPEAKER_0"},
            ],
        },
    }

    cfg = DiarizationConfig(enabled=True, engine="pyannote-ai")
    from meeting_minutes.system1.diarization_backends.pyannote_ai import (
        PyannoteAIBackend,
    )
    backend = PyannoteAIBackend(cfg)
    backend._client = fake_client

    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".flac") as f:
        result = backend.diarize(Path(f.name), num_speakers=2)

    assert result.num_speakers == 2
    assert len(result.segments) == 3
    # SPEAKER_0 short form normalized to SPEAKER_00
    assert {s.speaker for s in result.segments} == {"SPEAKER_00", "SPEAKER_01"}
    fake_client.diarize.assert_called_once()
    # num_speakers hint passed through
    _, kwargs = fake_client.diarize.call_args
    assert kwargs["num_speakers"] == 2
    assert kwargs["model"] == "community-1"  # default tier


def test_pyannote_ai_handles_missing_output(monkeypatch):
    monkeypatch.setenv("PYANNOTEAI_API_KEY", "fake")
    fake_client = MagicMock()
    fake_client.upload.return_value = "media://fake"
    fake_client.diarize.return_value = "job-2"
    fake_client.retrieve.return_value = {"status": "succeeded"}  # no output key

    cfg = DiarizationConfig(enabled=True, engine="pyannote-ai")
    from meeting_minutes.system1.diarization_backends.pyannote_ai import (
        PyannoteAIBackend,
    )
    backend = PyannoteAIBackend(cfg)
    backend._client = fake_client

    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".flac") as f:
        result = backend.diarize(Path(f.name))
    assert result.num_speakers == 0


def test_pyannote_ai_does_not_advertise_embeddings():
    """SPK-1 cross-meeting re-id should skip the cloud backend by default."""
    cfg = DiarizationConfig(enabled=True, engine="pyannote-ai")
    backend = select_backend(cfg)
    assert backend.supports_embeddings is False


# ---------------------------------------------------------------------------
# DiarizationBackend base contract
# ---------------------------------------------------------------------------


def test_diarization_backend_is_abstract():
    """Direct instantiation should fail — diarize() is abstract."""
    cfg = DiarizationConfig(enabled=False)
    with pytest.raises(TypeError):
        DiarizationBackend(cfg)


def test_empty_result_helper():
    cfg = DiarizationConfig(enabled=False)
    from meeting_minutes.system1.diarization_backends.pyannote_local import (
        PyannoteLocalBackend,
    )
    backend = PyannoteLocalBackend(cfg)
    empty = backend.empty_result()
    assert empty.segments == []
    assert empty.num_speakers == 0
