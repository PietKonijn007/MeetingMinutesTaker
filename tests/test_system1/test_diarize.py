"""Tests for the diarization engine."""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from meeting_minutes.config import DiarizationConfig
from meeting_minutes.models import DiarizationResult, DiarizationSegment
from meeting_minutes.system1.diarize import DiarizationEngine


# Feature: meeting-minutes-taker, Property 7: Diarization output consistency
@given(n_speakers=st.integers(min_value=1, max_value=6))
@settings(max_examples=50)
def test_diarization_speaker_label_pattern(n_speakers: int):
    """Property 7: Speaker labels match SPEAKER_XX and num_speakers is correct."""
    config = DiarizationConfig(enabled=True)
    engine = DiarizationEngine(config)

    # Mock the pipeline
    segments = []
    for i in range(n_speakers):
        turn = MagicMock()
        turn.start = float(i * 5)
        turn.end = float(i * 5 + 4)
        segments.append((turn, None, f"SPEAKER_{i:02d}"))

    mock_diarization = MagicMock()
    mock_diarization.itertracks = MagicMock(return_value=iter(segments))

    mock_pipeline = MagicMock(return_value=mock_diarization)
    engine._pipeline = mock_pipeline

    # Create dummy audio file
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".flac") as f:
        path = Path(f.name)
        result = engine.diarize(path)

    # Check pattern
    pattern = re.compile(r"^SPEAKER_\d{2}$")
    for seg in result.segments:
        assert pattern.match(seg.speaker), f"Invalid speaker label: {seg.speaker}"

    # Check num_speakers
    distinct_speakers = {seg.speaker for seg in result.segments}
    assert result.num_speakers == len(distinct_speakers)


def test_diarization_disabled_returns_empty():
    """Diarization with enabled=False returns empty result."""
    config = DiarizationConfig(enabled=False)
    engine = DiarizationEngine(config)

    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".flac") as f:
        result = engine.diarize(Path(f.name))

    assert result.num_speakers == 0
    assert result.segments == []


def test_diarization_missing_file_raises():
    """Diarizing a missing file raises FileNotFoundError."""
    config = DiarizationConfig(enabled=True)
    engine = DiarizationEngine(config)
    # Don't set _pipeline, so it would try to load — but we check file first
    with pytest.raises(FileNotFoundError):
        engine.diarize(Path("/nonexistent/audio.flac"))


def test_normalize_label_standard():
    """Standard SPEAKER_XX label stays unchanged."""
    assert DiarizationEngine._normalize_label("SPEAKER_00") == "SPEAKER_00"
    assert DiarizationEngine._normalize_label("SPEAKER_12") == "SPEAKER_12"


def test_normalize_label_nonstandard():
    """Non-standard labels get converted to SPEAKER_XX format."""
    label = DiarizationEngine._normalize_label("speaker_0")
    assert re.match(r"^SPEAKER_\d{2}$", label)


def test_merge_transcript_with_diarization():
    """Speaker labels are correctly assigned to transcript segments."""
    from meeting_minutes.models import TranscriptSegment, DiarizationResult, DiarizationSegment

    transcript_segments = [
        TranscriptSegment(id=0, start=0.0, end=5.0, text="Hello"),
        TranscriptSegment(id=1, start=5.5, end=10.0, text="World"),
    ]
    diarization = DiarizationResult(
        meeting_id="test",
        segments=[
            DiarizationSegment(start=0.0, end=5.5, speaker="SPEAKER_00"),
            DiarizationSegment(start=5.5, end=11.0, speaker="SPEAKER_01"),
        ],
        num_speakers=2,
    )

    result = DiarizationEngine.merge_transcript_with_diarization(
        transcript_segments, diarization
    )

    assert result[0].speaker == "SPEAKER_00"
    assert result[1].speaker == "SPEAKER_01"
