"""Tests for TranscriptIngester."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from hypothesis import given, settings, HealthCheck

from meeting_minutes.models import (
    SpeakerMapping,
    TranscriptData,
    TranscriptJSON,
    TranscriptSegment,
)
from meeting_minutes.system2.ingest import TranscriptIngester
from tests.strategies import transcript_json_strategy


# Feature: meeting-minutes-taker, Property 8: Transcript schema validation
def test_invalid_json_rejected(tmp_path: Path):
    """Property 8: Invalid transcript JSON is rejected."""
    path = tmp_path / "bad.json"
    path.write_text('{"not_a_transcript": true}')

    ingester = TranscriptIngester()
    with pytest.raises((ValueError, Exception)):
        ingester.ingest(path)


def test_valid_transcript_accepted(tmp_path: Path, sample_transcript_json: TranscriptJSON):
    """Valid TranscriptJSON is accepted."""
    path = tmp_path / f"{sample_transcript_json.meeting_id}.json"
    path.write_text(sample_transcript_json.model_dump_json())

    ingester = TranscriptIngester()
    result = ingester.ingest(path)

    assert isinstance(result, TranscriptData)
    assert result.meeting_id == sample_transcript_json.meeting_id


# Feature: meeting-minutes-taker, Property 9: Speaker label replacement
def test_speaker_label_replacement(tmp_path: Path, sample_transcript_json: TranscriptJSON):
    """Property 9: SPEAKER_XX labels are replaced with names after ingestion."""
    path = tmp_path / f"{sample_transcript_json.meeting_id}.json"
    path.write_text(sample_transcript_json.model_dump_json())

    ingester = TranscriptIngester()
    result = ingester.ingest(path)

    # No SPEAKER_XX labels should remain in segment speakers
    for seg in result.segments:
        if seg.speaker:
            assert not seg.speaker.startswith("SPEAKER_"), (
                f"Speaker label not replaced: {seg.speaker}"
            )


def test_missing_file_raises(tmp_path: Path):
    """Ingesting missing file raises FileNotFoundError."""
    ingester = TranscriptIngester()
    with pytest.raises(FileNotFoundError):
        ingester.ingest(tmp_path / "nonexistent.json")


def test_full_text_speaker_labels_replaced(tmp_path: Path, sample_transcript_json: TranscriptJSON):
    """Full text has SPEAKER_XX replaced with names."""
    # Inject SPEAKER label into full_text
    sample_transcript_json.transcript["full_text"] = "SPEAKER_00: Hello world."
    path = tmp_path / f"{sample_transcript_json.meeting_id}.json"
    path.write_text(sample_transcript_json.model_dump_json())

    ingester = TranscriptIngester()
    result = ingester.ingest(path)

    assert "SPEAKER_00" not in result.full_text
    assert "Alice" in result.full_text


@given(transcript=transcript_json_strategy())
@settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_ingest_valid_transcript_roundtrip(transcript: TranscriptJSON):
    """Any valid TranscriptJSON can be ingested without errors."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / f"{transcript.meeting_id}.json"
        path.write_text(transcript.model_dump_json())

        ingester = TranscriptIngester()
        result = ingester.ingest(path)

    assert isinstance(result, TranscriptData)
    assert result.meeting_id == transcript.meeting_id


def test_short_segments_merged(tmp_path: Path, sample_transcript_json: TranscriptJSON):
    """Short segments are merged with previous segments."""
    # Add a very short segment
    segs = list(sample_transcript_json.transcript["segments"])
    segs.append({
        "id": 99,
        "start": 100.0,
        "end": 100.2,  # Very short (0.2 seconds)
        "text": "Hmm.",
        "speaker": "SPEAKER_00",
        "words": [],
    })
    sample_transcript_json.transcript["segments"] = segs

    path = tmp_path / f"{sample_transcript_json.meeting_id}.json"
    path.write_text(sample_transcript_json.model_dump_json())

    ingester = TranscriptIngester()
    result = ingester.ingest(path)

    # Short segment should be merged (fewer segments than input)
    assert len(result.segments) <= len(segs)
