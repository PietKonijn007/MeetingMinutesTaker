"""Tests for TranscriptJSONWriter."""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from hypothesis import given, settings, HealthCheck

from meeting_minutes.models import (
    AudioRecordingResult,
    DiarizationResult,
    DiarizationSegment,
    SpeakerMapping,
    TranscriptJSON,
    TranscriptSegment,
    TranscriptionResult,
    WordTimestamp,
)
from meeting_minutes.system1.output import TranscriptJSONWriter
from tests.strategies import transcript_json_strategy


# Feature: meeting-minutes-taker, Property 5: Transcript JSON schema validity
def test_transcript_json_schema_validity(tmp_path: Path):
    """Property 5: Written TranscriptJSON has all required top-level fields."""
    writer = TranscriptJSONWriter()
    recording = _make_recording("test-id-123", str(tmp_path / "test.flac"))
    transcription = _make_transcription("test-id-123")
    diarization = _make_diarization()

    path = writer.write(
        meeting_id="test-id-123",
        recording=recording,
        transcription=transcription,
        diarization=diarization,
        output_dir=tmp_path,
    )

    assert path.exists()
    data = json.loads(path.read_text())

    # Required top-level fields
    assert "schema_version" in data
    assert "meeting_id" in data
    assert "metadata" in data
    assert "speakers" in data
    assert "meeting_type" in data
    assert "transcript" in data
    assert "processing" in data

    # Processing block
    assert "created_at" in data["processing"]
    assert "processing_time_seconds" in data["processing"]
    assert "pipeline_version" in data["processing"]

    # Transcript contains segments and full_text
    assert "segments" in data["transcript"]
    assert "full_text" in data["transcript"]


def test_output_path_is_meeting_id(tmp_path: Path):
    """Output file is named {meeting_id}.json."""
    writer = TranscriptJSONWriter()
    mid = "abc123def456"
    recording = _make_recording(mid, str(tmp_path / f"{mid}.flac"))
    transcription = _make_transcription(mid)

    path = writer.write(
        meeting_id=mid,
        recording=recording,
        transcription=transcription,
        diarization=None,
        output_dir=tmp_path,
    )

    assert path.name == f"{mid}.json"


def test_output_without_diarization(tmp_path: Path):
    """Writing without diarization still succeeds."""
    writer = TranscriptJSONWriter()
    mid = "no-diarize-test"
    recording = _make_recording(mid, str(tmp_path / f"{mid}.flac"))
    transcription = _make_transcription(mid)

    path = writer.write(
        meeting_id=mid,
        recording=recording,
        transcription=transcription,
        diarization=None,
        output_dir=tmp_path,
    )

    assert path.exists()
    data = json.loads(path.read_text())
    assert data["speakers"] == []


# Feature: meeting-minutes-taker, Property 6: Transcript JSON round-trip
@given(transcript=transcript_json_strategy())
@settings(max_examples=100)
def test_transcript_json_round_trip(transcript: TranscriptJSON):
    """Property 6: TranscriptJSON serializes and deserializes without data loss."""
    serialized = transcript.model_dump_json()
    restored = TranscriptJSON.model_validate_json(serialized)

    assert restored.meeting_id == transcript.meeting_id
    assert restored.schema_version == transcript.schema_version
    assert restored.meeting_type == transcript.meeting_type
    assert restored.meeting_type_confidence == transcript.meeting_type_confidence
    assert len(restored.speakers) == len(transcript.speakers)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_recording(meeting_id: str, audio_file: str) -> AudioRecordingResult:
    now = datetime.now(timezone.utc)
    return AudioRecordingResult(
        meeting_id=meeting_id,
        audio_file=audio_file,
        start_time=now,
        end_time=now,
        duration_seconds=60.0,
        sample_rate=16000,
        recording_device="default",
    )


def _make_transcription(meeting_id: str) -> TranscriptionResult:
    return TranscriptionResult(
        meeting_id=meeting_id,
        segments=[
            TranscriptSegment(
                id=0,
                start=0.0,
                end=5.0,
                text="Hello world",
                words=[WordTimestamp(word="Hello", start=0.0, end=0.5, confidence=0.99)],
            )
        ],
        full_text="Hello world",
        language="en",
        transcription_engine="faster-whisper",
        transcription_model="medium",
        processing_time_seconds=1.5,
    )


def _make_diarization() -> DiarizationResult:
    return DiarizationResult(
        meeting_id="test",
        segments=[
            DiarizationSegment(start=0.0, end=5.0, speaker="SPEAKER_00"),
        ],
        num_speakers=1,
    )
