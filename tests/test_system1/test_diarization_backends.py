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
    """With fallback disabled, missing API key surfaces as an empty result —
    the recording pipeline keeps moving and the user sees the warning."""
    monkeypatch.delenv("PYANNOTEAI_API_KEY", raising=False)
    cfg = DiarizationConfig(enabled=True, engine="pyannote-ai", fallback_to_local=False)
    from meeting_minutes.system1.diarization_backends.pyannote_ai import (
        PyannoteAIBackend,
    )
    backend = PyannoteAIBackend(cfg)
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".flac") as f:
        result = backend.diarize(Path(f.name))
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
    """A response with no ``output`` key shouldn't crash. With fallback
    disabled we just produce an empty result; the warning still goes to
    the log so the user can investigate."""
    monkeypatch.setenv("PYANNOTEAI_API_KEY", "fake")
    fake_client = MagicMock()
    fake_client.upload.return_value = "media://fake"
    fake_client.diarize.return_value = "job-2"
    fake_client.retrieve.return_value = {"status": "succeeded"}  # no output key

    cfg = DiarizationConfig(enabled=True, engine="pyannote-ai", fallback_to_local=False)
    from meeting_minutes.system1.diarization_backends.pyannote_ai import (
        PyannoteAIBackend,
    )
    backend = PyannoteAIBackend(cfg)
    backend._client = fake_client

    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".flac") as f:
        result = backend.diarize(Path(f.name))
    assert result.num_speakers == 0


def test_pyannote_ai_supports_embeddings_when_fallback_enabled():
    """With fallback enabled, advertise True so SPK-1 attempts centroid
    matching on runs that did go through local pyannote."""
    cfg = DiarizationConfig(enabled=True, engine="pyannote-ai", fallback_to_local=True)
    backend = select_backend(cfg)
    assert backend.supports_embeddings is True


def test_pyannote_ai_does_not_advertise_embeddings_when_fallback_disabled():
    """With fallback off, we'll only ever return cloud results, and the
    cloud path doesn't fetch voiceprints by default."""
    cfg = DiarizationConfig(enabled=True, engine="pyannote-ai", fallback_to_local=False)
    backend = select_backend(cfg)
    assert backend.supports_embeddings is False


# ---------------------------------------------------------------------------
# pyannote-ai → local fallback
# ---------------------------------------------------------------------------


def _empty_audio_file(suffix: str = ".flac"):
    """Helper: yield a path to a real (zero-byte) tempfile that exists."""
    import tempfile
    return tempfile.NamedTemporaryFile(suffix=suffix)


def test_fallback_runs_local_when_cloud_client_init_fails(monkeypatch):
    """Network down / SDK missing / bad auth → fall back to local pyannote.

    We force ``_get_client`` to raise (simulating an auth error or missing
    package) and assert that the local backend's ``diarize`` is the one
    that ends up returning the result.
    """
    monkeypatch.setenv("PYANNOTEAI_API_KEY", "fake")
    cfg = DiarizationConfig(enabled=True, engine="pyannote-ai", fallback_to_local=True)
    from meeting_minutes.system1.diarization_backends.pyannote_ai import (
        PyannoteAIBackend,
    )
    backend = PyannoteAIBackend(cfg)

    # Simulate cloud client construction blowing up.
    def _explode(self):
        raise RuntimeError("simulated network failure")
    monkeypatch.setattr(PyannoteAIBackend, "_get_client", _explode)

    # Local fallback returns a synthetic non-empty result so we can detect
    # it ran.
    expected = MagicMock()
    expected.segments = ["s1", "s2"]
    expected.num_speakers = 2

    fake_local = MagicMock()
    fake_local.diarize.return_value = expected
    fake_local.last_cluster_embeddings = {"SPEAKER_00": [1.0]}
    backend._local_fallback = fake_local

    with _empty_audio_file() as f:
        result = backend.diarize(Path(f.name), num_speakers=2)

    fake_local.diarize.assert_called_once()
    # Hint should be forwarded to the local fallback unchanged.
    _, kwargs = fake_local.diarize.call_args
    assert kwargs.get("num_speakers") == 2
    assert result is expected
    # Embeddings produced by the local fallback are surfaced on the engine
    # so SPK-1 cross-meeting re-id can use them.
    assert backend.last_cluster_embeddings == {"SPEAKER_00": [1.0]}


def test_fallback_runs_local_on_api_call_failure(monkeypatch):
    """An auth failure / 5xx mid-call → fall back to local pyannote."""
    monkeypatch.setenv("PYANNOTEAI_API_KEY", "fake")
    cfg = DiarizationConfig(enabled=True, engine="pyannote-ai", fallback_to_local=True)
    from meeting_minutes.system1.diarization_backends.pyannote_ai import (
        PyannoteAIBackend,
    )
    backend = PyannoteAIBackend(cfg)

    fake_client = MagicMock()
    fake_client.upload.return_value = "media://fake"
    fake_client.diarize.side_effect = RuntimeError("HTTPError 401 unauthorized")
    backend._client = fake_client

    expected = MagicMock()
    expected.segments = ["s1"]
    expected.num_speakers = 1
    fake_local = MagicMock()
    fake_local.diarize.return_value = expected
    fake_local.last_cluster_embeddings = {}
    backend._local_fallback = fake_local

    with _empty_audio_file() as f:
        result = backend.diarize(Path(f.name))

    assert fake_local.diarize.called
    assert result is expected


def test_fallback_runs_local_when_cloud_returns_empty(monkeypatch):
    """A 200 response with zero segments is no better than a failure —
    a downstream pipeline that expected diarization would silently produce
    a transcript with no speaker labels. Treat empty as a fall-back trigger."""
    monkeypatch.setenv("PYANNOTEAI_API_KEY", "fake")
    cfg = DiarizationConfig(enabled=True, engine="pyannote-ai", fallback_to_local=True)
    from meeting_minutes.system1.diarization_backends.pyannote_ai import (
        PyannoteAIBackend,
    )
    backend = PyannoteAIBackend(cfg)

    fake_client = MagicMock()
    fake_client.upload.return_value = "media://fake"
    fake_client.diarize.return_value = "job-1"
    fake_client.retrieve.return_value = {"output": {"diarization": []}}  # empty
    backend._client = fake_client

    expected = MagicMock()
    expected.segments = ["s1", "s2", "s3"]
    expected.num_speakers = 3
    fake_local = MagicMock()
    fake_local.diarize.return_value = expected
    fake_local.last_cluster_embeddings = {}
    backend._local_fallback = fake_local

    with _empty_audio_file() as f:
        result = backend.diarize(Path(f.name))

    assert fake_local.diarize.called
    assert result is expected


def test_fallback_disabled_returns_empty_on_cloud_failure(monkeypatch):
    """When the user explicitly disables fallback, we surface an empty
    result instead of silently rerouting — easier to detect cloud outages."""
    monkeypatch.setenv("PYANNOTEAI_API_KEY", "fake")
    cfg = DiarizationConfig(enabled=True, engine="pyannote-ai", fallback_to_local=False)
    from meeting_minutes.system1.diarization_backends.pyannote_ai import (
        PyannoteAIBackend,
    )
    backend = PyannoteAIBackend(cfg)

    def _explode(self):
        raise RuntimeError("simulated outage")
    monkeypatch.setattr(PyannoteAIBackend, "_get_client", _explode)

    backend._local_fallback = MagicMock()
    backend._local_fallback.diarize = MagicMock()  # must NOT be called

    with _empty_audio_file() as f:
        result = backend.diarize(Path(f.name))

    assert backend._local_fallback.diarize.call_count == 0
    assert result.segments == []
    assert result.num_speakers == 0


def test_cloud_success_does_not_invoke_local_fallback(monkeypatch):
    """The fast path: when the API returns a valid non-empty diarization,
    the local backend must never even be constructed (~3-5s import cost)."""
    monkeypatch.setenv("PYANNOTEAI_API_KEY", "fake")
    cfg = DiarizationConfig(enabled=True, engine="pyannote-ai", fallback_to_local=True)
    from meeting_minutes.system1.diarization_backends.pyannote_ai import (
        PyannoteAIBackend,
    )
    backend = PyannoteAIBackend(cfg)

    fake_client = MagicMock()
    fake_client.upload.return_value = "media://fake"
    fake_client.diarize.return_value = "job-1"
    fake_client.retrieve.return_value = {
        "output": {
            "diarization": [
                {"start": 0.0, "end": 5.0, "speaker": "SPEAKER_0"},
                {"start": 5.0, "end": 10.0, "speaker": "SPEAKER_1"},
            ],
        },
    }
    backend._client = fake_client

    with _empty_audio_file() as f:
        result = backend.diarize(Path(f.name), num_speakers=2)

    assert result.num_speakers == 2
    # Local fallback never instantiated — proves the fast path is preserved.
    assert backend._local_fallback is None


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
