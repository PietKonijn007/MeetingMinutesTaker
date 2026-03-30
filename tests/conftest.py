"""Shared pytest fixtures."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from meeting_minutes.config import AppConfig
from meeting_minutes.models import (
    ActionItem,
    ActionItemStatus,
    AudioRecordingResult,
    Decision,
    DiarizationResult,
    DiarizationSegment,
    DiscussionPoint,
    FollowUp,
    LLMResponse,
    LLMUsage,
    MeetingEffectiveness,
    MinutesData,
    MinutesJSON,
    MinutesMetadata,
    MinutesSection,
    ParticipantInfo,
    ParsedMinutes,
    QualityReport,
    RiskConcern,
    SpeakerMapping,
    TranscriptData,
    TranscriptJSON,
    TranscriptMetadata,
    TranscriptSegment,
    TranscriptionResult,
    WordTimestamp,
)
from meeting_minutes.system3.db import get_session_factory


@pytest.fixture
def sample_meeting_id():
    return str(uuid.uuid4())


@pytest.fixture
def sample_transcript_metadata():
    now = datetime.now(timezone.utc)
    return TranscriptMetadata(
        timestamp_start=now,
        timestamp_end=now,
        duration_seconds=600.0,
        language="en",
        transcription_engine="faster-whisper",
        transcription_model="medium",
        audio_file="/tmp/test.flac",
        recording_device="default",
    )


@pytest.fixture
def sample_transcript_json(sample_meeting_id, sample_transcript_metadata):
    segments = [
        TranscriptSegment(
            id=0,
            start=0.0,
            end=5.0,
            speaker="SPEAKER_00",
            text="Hello, let's start the meeting.",
            words=[
                WordTimestamp(word="Hello", start=0.0, end=0.5, confidence=0.99),
                WordTimestamp(word="let's", start=0.6, end=1.0, confidence=0.98),
            ],
        ),
        TranscriptSegment(
            id=1,
            start=5.1,
            end=10.0,
            speaker="SPEAKER_01",
            text="Thanks. I completed the API integration.",
            words=[],
        ),
    ]
    return TranscriptJSON(
        meeting_id=sample_meeting_id,
        metadata=sample_transcript_metadata,
        speakers=[
            SpeakerMapping(label="SPEAKER_00", name="Alice", confidence=0.9),
            SpeakerMapping(label="SPEAKER_01", name="Bob", confidence=0.85),
        ],
        meeting_type="standup",
        meeting_type_confidence=0.85,
        transcript={
            "segments": [s.model_dump() for s in segments],
            "full_text": "Hello, let's start the meeting. Thanks. I completed the API integration.",
        },
        processing={
            "created_at": datetime.now(timezone.utc).isoformat(),
            "processing_time_seconds": 12.5,
            "pipeline_version": "0.1.0",
        },
    )


@pytest.fixture
def sample_transcript_data(sample_transcript_json):
    return TranscriptData(
        meeting_id=sample_transcript_json.meeting_id,
        transcript_json=sample_transcript_json,
        full_text=sample_transcript_json.transcript["full_text"],
        segments=[
            TranscriptSegment(**s)
            for s in sample_transcript_json.transcript["segments"]
        ],
        speakers=["Alice", "Bob"],
    )


@pytest.fixture
def sample_parsed_minutes(sample_meeting_id):
    return ParsedMinutes(
        meeting_id=sample_meeting_id,
        summary="This was a daily standup meeting where Alice and Bob discussed their progress.",
        sections=[
            MinutesSection(heading="Alice", content="**Done:** API work\n**Today:** Testing\n**Blockers:** None", type="discussion"),
            MinutesSection(heading="Bob", content="**Done:** Code review\n**Today:** Deployment\n**Blockers:** None", type="discussion"),
        ],
        action_items=[
            ActionItem(description="Deploy to staging", owner="Bob", due_date="2025-01-15"),
        ],
        decisions=[
            Decision(description="Use PostgreSQL for production"),
        ],
        key_topics=["API integration", "deployment", "testing"],
        raw_llm_response="## Summary\nThis was a daily standup meeting where Alice and Bob discussed their progress.\n\n## Alice\n**Done:** API work\n**Today:** Testing\n**Blockers:** None\n\n## Bob\n**Done:** Code review\n**Today:** Deployment\n**Blockers:** None\n\n## Action Items\n- [ ] Deploy to staging — Owner: Bob (Due: 2025-01-15)\n\n## Decisions\n- Use PostgreSQL for production",
        meeting_context={"title": "Daily Standup", "date": "2025-01-10", "attendees": ["Alice", "Bob"]},
    )


@pytest.fixture
def sample_llm_response():
    return LLMResponse(
        text="## Summary\nTest meeting summary.\n\n## Action Items\n- [ ] Test action — Owner: Alice\n\n## Decisions\n- Test decision",
        provider="anthropic",
        model="claude-sonnet-4-6-20250514",
        input_tokens=1000,
        output_tokens=500,
        cost_usd=0.005,
        processing_time_seconds=2.5,
    )


@pytest.fixture
def sample_quality_report():
    return QualityReport(
        passed=True,
        score=0.9,
        speaker_coverage=1.0,
        length_ratio=0.15,
        hallucination_flags=[],
    )


@pytest.fixture
def sample_minutes_json(sample_meeting_id):
    return MinutesJSON(
        meeting_id=sample_meeting_id,
        generated_at=datetime.now(timezone.utc),
        meeting_type="standup",
        metadata=MinutesMetadata(
            title="Daily Standup",
            date="2025-01-10",
            duration="15 minutes",
            attendees=["Alice", "Bob"],
            organizer="Alice",
        ),
        summary="Brief standup covering progress and blockers.",
        sections=[
            MinutesSection(heading="Discussion", content="Team discussed progress.", type="discussion"),
        ],
        action_items=[
            ActionItem(description="Deploy to staging", owner="Bob", status=ActionItemStatus.OPEN),
        ],
        decisions=[
            Decision(description="Use PostgreSQL"),
        ],
        key_topics=["deployment", "API"],
        minutes_markdown="# Daily Standup\n\nBrief standup...",
        llm=LLMUsage(
            provider="anthropic",
            model="claude-sonnet-4-6-20250514",
            tokens_used={"input": 1000, "output": 500},
            cost_usd=0.005,
            processing_time_seconds=2.5,
        ),
    )


@pytest.fixture
def sample_minutes_data(sample_minutes_json, sample_transcript_json):
    return MinutesData(
        minutes_json=sample_minutes_json,
        transcript_json=sample_transcript_json,
        json_path="/tmp/test_minutes.json",
        md_path="/tmp/test_minutes.md",
    )


@pytest.fixture
def db_session():
    """In-memory SQLite session for tests."""
    session_factory = get_session_factory("sqlite:///:memory:")
    session = session_factory()
    yield session
    session.close()


@pytest.fixture
def storage(db_session):
    from meeting_minutes.system3.storage import StorageEngine
    return StorageEngine(db_session)


@pytest.fixture
def search_engine(db_session):
    from meeting_minutes.system3.search import SearchEngine
    return SearchEngine(db_session)


@pytest.fixture
def default_config():
    return AppConfig()


@pytest.fixture
def transcript_json_file(tmp_path, sample_transcript_json):
    """Write TranscriptJSON to a temp file and return the path."""
    path = tmp_path / f"{sample_transcript_json.meeting_id}.json"
    path.write_text(sample_transcript_json.model_dump_json(indent=2))
    return path


@pytest.fixture
def minutes_json_file(tmp_path, sample_minutes_json):
    """Write MinutesJSON to a temp file and return the path."""
    path = tmp_path / f"{sample_minutes_json.meeting_id}.json"
    path.write_text(sample_minutes_json.model_dump_json(indent=2))
    return path
