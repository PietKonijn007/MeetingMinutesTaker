"""Tests for the transcription engine."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from meeting_minutes.config import TranscriptionConfig
from meeting_minutes.models import TranscriptSegment, TranscriptionResult, WordTimestamp
from meeting_minutes.system1.transcribe import TranscriptionEngine


def _make_mock_model(segments_data=None):
    """Create a mock faster-whisper model."""
    if segments_data is None:
        segments_data = [
            {
                "id": 0,
                "start": 0.0,
                "end": 5.0,
                "text": " Hello world.",
                "words": [
                    MagicMock(word=" Hello", start=0.0, end=0.5, probability=0.99),
                    MagicMock(word=" world", start=0.6, end=1.0, probability=0.98),
                ],
            }
        ]

    mock_segs = []
    for d in segments_data:
        seg = MagicMock()
        seg.id = d["id"]
        seg.start = d["start"]
        seg.end = d["end"]
        seg.text = d["text"]
        seg.words = d.get("words", [])
        mock_segs.append(seg)

    mock_info = MagicMock()
    mock_info.language = "en"

    model = MagicMock()
    model.transcribe = MagicMock(return_value=(iter(mock_segs), mock_info))
    return model


# Feature: meeting-minutes-taker, Property 4: Transcription output completeness
def test_transcription_output_completeness(tmp_path: Path):
    """Property 4: Every segment has words with timestamps and result has language."""
    config = TranscriptionConfig()
    engine = TranscriptionEngine(config)
    engine._model = _make_mock_model()

    audio_path = tmp_path / "test.flac"
    audio_path.write_bytes(b"\x00" * 100)  # dummy audio

    result = engine.transcribe(audio_path)

    assert isinstance(result, TranscriptionResult)
    assert result.language is not None
    assert len(result.language) > 0

    for seg in result.segments:
        assert seg.start >= 0
        assert seg.end >= seg.start
        for word in seg.words:
            assert word.start >= 0
            assert word.end >= word.start
            assert 0.0 <= word.confidence <= 1.0


def test_transcription_result_has_full_text(tmp_path: Path):
    """TranscriptionResult contains full_text."""
    config = TranscriptionConfig()
    engine = TranscriptionEngine(config)
    engine._model = _make_mock_model()

    audio_path = tmp_path / "test.flac"
    audio_path.write_bytes(b"\x00" * 100)

    result = engine.transcribe(audio_path)
    assert result.full_text == "Hello world."


def test_transcription_missing_file_raises():
    """Transcribing a missing file raises FileNotFoundError."""
    config = TranscriptionConfig()
    engine = TranscriptionEngine(config)
    engine._model = _make_mock_model()

    with pytest.raises(FileNotFoundError):
        engine.transcribe(Path("/nonexistent/audio.flac"))


def test_transcription_processing_time(tmp_path: Path):
    """Processing time is recorded."""
    config = TranscriptionConfig()
    engine = TranscriptionEngine(config)
    engine._model = _make_mock_model()

    audio_path = tmp_path / "test.flac"
    audio_path.write_bytes(b"\x00" * 100)

    result = engine.transcribe(audio_path)
    assert result.processing_time_seconds >= 0.0


def test_transcription_model_name_in_result(tmp_path: Path):
    """Result includes model name from config."""
    config = TranscriptionConfig(whisper_model="tiny")
    engine = TranscriptionEngine(config)
    engine._model = _make_mock_model()

    audio_path = tmp_path / "test.flac"
    audio_path.write_bytes(b"\x00" * 100)

    result = engine.transcribe(audio_path)
    assert result.transcription_model == "tiny"
    assert result.transcription_engine == "faster-whisper"


def test_transcription_empty_audio(tmp_path: Path):
    """Transcription with no segments returns empty result."""
    config = TranscriptionConfig()
    engine = TranscriptionEngine(config)
    engine._model = _make_mock_model(segments_data=[])

    audio_path = tmp_path / "test.flac"
    audio_path.write_bytes(b"\x00" * 100)

    result = engine.transcribe(audio_path)
    assert result.segments == []
    assert result.full_text == ""
