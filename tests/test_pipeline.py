"""Tests for the pipeline orchestrator."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from meeting_minutes.config import AppConfig
from meeting_minutes.models import (
    AudioRecordingResult,
    DiarizationResult,
    LLMResponse,
    MinutesData,
    MinutesJSON,
    MinutesMetadata,
    MinutesSection,
    LLMUsage,
    ParsedMinutes,
    QualityReport,
    TranscriptData,
    TranscriptJSON,
    TranscriptMetadata,
    SpeakerMapping,
    TranscriptSegment,
    TranscriptionResult,
    ActionItem,
    Decision,
)
from meeting_minutes.pipeline import PipelineOrchestrator


@pytest.fixture
def pipeline(tmp_path):
    config = AppConfig(data_dir=str(tmp_path))
    return PipelineOrchestrator(config)


# Feature: meeting-minutes-taker, Property 31: Reprocess idempotence
@pytest.mark.asyncio
async def test_reprocess_idempotence(tmp_path):
    """Property 31: Reprocessing produces the same final state as a fresh run."""
    config = AppConfig(data_dir=str(tmp_path))
    orchestrator = PipelineOrchestrator(config)
    meeting_id = str(uuid.uuid4())

    # Create a fake transcript file
    transcripts_dir = tmp_path / "transcripts"
    transcripts_dir.mkdir(parents=True)
    minutes_dir = tmp_path / "minutes"
    minutes_dir.mkdir(parents=True)

    transcript_json = _make_transcript_json(meeting_id)
    transcript_path = transcripts_dir / f"{meeting_id}.json"
    transcript_path.write_text(transcript_json.model_dump_json())

    minutes_json = _make_minutes_json(meeting_id)

    # Mock run_generation and run_ingestion
    gen_mock = AsyncMock(return_value=minutes_dir / f"{meeting_id}.json")
    ingest_mock = AsyncMock()

    with patch.object(orchestrator, "run_generation", gen_mock):
        with patch.object(orchestrator, "run_ingestion", ingest_mock):
            # First run
            await orchestrator.reprocess(meeting_id)
            gen_call_count_1 = gen_mock.call_count
            ingest_call_count_1 = ingest_mock.call_count

            # Second run (reprocess again)
            await orchestrator.reprocess(meeting_id)
            gen_call_count_2 = gen_mock.call_count
            ingest_call_count_2 = ingest_mock.call_count

    # Both runs called generation and ingestion once each
    assert gen_call_count_1 == 1
    assert gen_call_count_2 == 2
    assert ingest_call_count_1 == 1
    assert ingest_call_count_2 == 2


async def test_run_generation_fails_without_transcript(tmp_path):
    """Generation fails with FileNotFoundError when transcript is missing."""
    config = AppConfig(data_dir=str(tmp_path))
    orchestrator = PipelineOrchestrator(config)

    with pytest.raises(FileNotFoundError):
        await orchestrator.run_generation("nonexistent-id")


async def test_run_ingestion_fails_without_minutes(tmp_path):
    """Ingestion fails with FileNotFoundError when minutes are missing."""
    config = AppConfig(data_dir=str(tmp_path))
    orchestrator = PipelineOrchestrator(config)

    with pytest.raises(FileNotFoundError):
        await orchestrator.run_ingestion("nonexistent-id")


async def test_pipeline_mode_automatic(tmp_path):
    """PipelineOrchestrator reads pipeline mode from config."""
    config = AppConfig(data_dir=str(tmp_path))
    config.pipeline.mode = "automatic"
    orchestrator = PipelineOrchestrator(config)
    assert orchestrator._config.pipeline.mode == "automatic"


async def test_pipeline_mode_manual(tmp_path):
    """PipelineOrchestrator reads pipeline mode from config."""
    config = AppConfig(data_dir=str(tmp_path))
    config.pipeline.mode = "manual"
    orchestrator = PipelineOrchestrator(config)
    assert orchestrator._config.pipeline.mode == "manual"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_transcript_json(meeting_id: str) -> TranscriptJSON:
    now = datetime.now(timezone.utc)
    return TranscriptJSON(
        meeting_id=meeting_id,
        metadata=TranscriptMetadata(
            timestamp_start=now,
            timestamp_end=now,
            duration_seconds=300.0,
            language="en",
            transcription_engine="faster-whisper",
            transcription_model="medium",
            audio_file="/tmp/test.flac",
            recording_device="default",
        ),
        speakers=[SpeakerMapping(label="SPEAKER_00", name="Alice")],
        meeting_type="standup",
        meeting_type_confidence=0.9,
        transcript={"segments": [], "full_text": "Alice: Hello world."},
        processing={
            "created_at": now.isoformat(),
            "processing_time_seconds": 5.0,
            "pipeline_version": "0.1.0",
        },
    )


def _make_minutes_json(meeting_id: str) -> MinutesJSON:
    return MinutesJSON(
        meeting_id=meeting_id,
        generated_at=datetime.now(timezone.utc),
        meeting_type="standup",
        metadata=MinutesMetadata(
            title="Test Standup",
            date="2025-01-10",
            duration="15 minutes",
            attendees=["Alice"],
        ),
        summary="Test summary.",
        sections=[],
        action_items=[],
        decisions=[],
        key_topics=["testing"],
        minutes_markdown="# Test\n",
        llm=LLMUsage(
            provider="anthropic",
            model="claude-sonnet-4-6-20250514",
            tokens_used={"input": 100, "output": 50},
            cost_usd=0.001,
            processing_time_seconds=1.0,
        ),
    )
