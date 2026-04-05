"""Shared Pydantic data models for the Meeting Minutes Taker application."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class MeetingType(str, Enum):
    STANDUP = "standup"
    ONE_ON_ONE = "one_on_one"
    TEAM_MEETING = "team_meeting"
    DECISION_MEETING = "decision_meeting"
    CUSTOMER_MEETING = "customer_meeting"
    BRAINSTORM = "brainstorm"
    RETROSPECTIVE = "retrospective"
    PLANNING = "planning"
    OTHER = "other"


class ActionItemStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    CANCELLED = "cancelled"


class ReviewStatus(str, Enum):
    DRAFT = "draft"
    REVIEWED = "reviewed"
    APPROVED = "approved"


# ---------------------------------------------------------------------------
# Transcript data models
# ---------------------------------------------------------------------------


class WordTimestamp(BaseModel):
    word: str
    start: float
    end: float
    confidence: float


class TranscriptSegment(BaseModel):
    id: int
    start: float
    end: float
    speaker: str | None = None
    text: str
    words: list[WordTimestamp] = []


class SpeakerMapping(BaseModel):
    label: str  # e.g. "SPEAKER_00"
    name: str | None = None
    email: str | None = None
    confidence: float = 0.0


class TranscriptMetadata(BaseModel):
    timestamp_start: datetime
    timestamp_end: datetime
    duration_seconds: float
    platform: str | None = None
    language: str
    transcription_engine: str
    transcription_model: str
    audio_file: str
    recording_device: str


class TranscriptJSON(BaseModel):
    schema_version: str = "1.0"
    meeting_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    metadata: TranscriptMetadata
    speakers: list[SpeakerMapping] = []
    meeting_type: str = "other"
    meeting_type_confidence: float = 0.0
    transcript: dict  # {"segments": [...], "full_text": "..."}
    processing: dict  # {"created_at": ..., "processing_time_seconds": ..., "pipeline_version": ...}


# ---------------------------------------------------------------------------
# Minutes data models
# ---------------------------------------------------------------------------


class ActionItem(BaseModel):
    id: str = Field(default_factory=lambda: f"ai-{uuid.uuid4().hex[:6]}")
    description: str
    owner: str | None = None
    due_date: str | None = None
    status: ActionItemStatus = ActionItemStatus.OPEN
    mentioned_at_seconds: float | None = None
    priority: str | None = None  # high, medium, low
    transcript_segment_ids: list[int] = []


class Decision(BaseModel):
    id: str = Field(default_factory=lambda: f"d-{uuid.uuid4().hex[:6]}")
    description: str
    made_by: str | None = None
    mentioned_at_seconds: float | None = None
    rationale: str | None = None
    confidence: str | None = None  # high, medium, low
    transcript_segment_ids: list[int] = []


class MinutesSection(BaseModel):
    heading: str
    content: str
    type: str | None = None


class MinutesMetadata(BaseModel):
    title: str
    date: str
    duration: str
    attendees: list[str]
    organizer: str | None = None


class LLMUsage(BaseModel):
    provider: str
    model: str
    tokens_used: dict  # {"input": int, "output": int}
    cost_usd: float
    processing_time_seconds: float


class ParticipantInfo(BaseModel):
    name: str
    role: str | None = None  # facilitator, presenter, contributor, observer
    sentiment: str | None = None  # positive, neutral, negative, mixed


class DiscussionPoint(BaseModel):
    topic: str
    summary: str
    participants: list[str] = []
    sentiment: str | None = None
    transcript_segment_ids: list[int] = []


class StructuredDecision(BaseModel):
    description: str
    made_by: str | None = None
    rationale: str | None = None
    confidence: str | None = None  # high, medium, low
    transcript_segment_ids: list[int] = []


class StructuredActionItem(BaseModel):
    description: str
    owner: str | None = None
    due_date: str | None = None
    priority: str | None = None  # high, medium, low
    transcript_segment_ids: list[int] = []


class RiskConcern(BaseModel):
    description: str
    raised_by: str | None = None


class FollowUp(BaseModel):
    description: str
    owner: str | None = None
    timeframe: str | None = None


class MeetingEffectiveness(BaseModel):
    had_clear_agenda: bool | None = None
    decisions_made: int = 0
    action_items_assigned: int = 0
    unresolved_items: int = 0


class StructuredMinutesResponse(BaseModel):
    """The complete schema the LLM fills via tool_use."""
    title: str = ""
    summary: str = ""
    meeting_type_suggestion: str | None = None
    sentiment: str | None = None  # constructive, positive, neutral, tense, negative
    participants: list[ParticipantInfo] = []
    discussion_points: list[DiscussionPoint] = []
    decisions: list[StructuredDecision] = []
    action_items: list[StructuredActionItem] = []
    risks_and_concerns: list[RiskConcern] = []
    follow_ups: list[FollowUp] = []
    key_topics: list[str] = []
    parking_lot: list[str] = []
    meeting_effectiveness: MeetingEffectiveness | None = None


class MinutesJSON(BaseModel):
    schema_version: str = "1.0"
    meeting_id: str
    minutes_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    generated_at: datetime
    meeting_type: str
    metadata: MinutesMetadata
    summary: str
    sections: list[MinutesSection]
    action_items: list[ActionItem]
    decisions: list[Decision]
    key_topics: list[str]
    minutes_markdown: str
    llm: LLMUsage
    sentiment: str | None = None
    participants: list[ParticipantInfo] = []
    discussion_points: list[DiscussionPoint] = []
    risks_and_concerns: list[RiskConcern] = []
    follow_ups: list[FollowUp] = []
    parking_lot: list[str] = []
    meeting_effectiveness: MeetingEffectiveness | None = None
    structured_data: dict | None = None


# ---------------------------------------------------------------------------
# Search models
# ---------------------------------------------------------------------------


class SearchQuery(BaseModel):
    raw_query: str
    fts_query: str = ""
    meeting_type: str | None = None
    after_date: datetime | None = None
    before_date: datetime | None = None
    attendee: str | None = None
    action_item_status: str | None = None
    limit: int = 20
    offset: int = 0


class SearchResult(BaseModel):
    meeting_id: str
    title: str
    date: datetime
    meeting_type: str
    snippet: str
    rank: float


class SearchResults(BaseModel):
    results: list[SearchResult]
    total_count: int
    query: SearchQuery


# ---------------------------------------------------------------------------
# Internal pipeline result types (dataclass-style Pydantic models)
# ---------------------------------------------------------------------------


class AudioRecordingResult(BaseModel):
    meeting_id: str
    audio_file: str
    start_time: datetime
    end_time: datetime
    duration_seconds: float
    sample_rate: int
    recording_device: str
    format: str = "flac"


class TranscriptionResult(BaseModel):
    meeting_id: str
    segments: list[TranscriptSegment]
    full_text: str
    language: str
    transcription_engine: str
    transcription_model: str
    processing_time_seconds: float


class DiarizationSegment(BaseModel):
    start: float
    end: float
    speaker: str  # e.g. "SPEAKER_00"


class DiarizationResult(BaseModel):
    meeting_id: str
    segments: list[DiarizationSegment]
    num_speakers: int


class TranscriptData(BaseModel):
    """Pre-processed transcript used by System 2."""

    meeting_id: str
    transcript_json: TranscriptJSON
    full_text: str
    segments: list[TranscriptSegment]
    speakers: list[str]  # resolved speaker names


class ParsedMinutes(BaseModel):
    """Output of MinutesParser."""

    meeting_id: str
    title: str = ""
    summary: str
    sections: list[MinutesSection]
    action_items: list[ActionItem]
    decisions: list[Decision]
    key_topics: list[str]
    raw_llm_response: str
    meeting_context: dict = Field(default_factory=dict)
    sentiment: str | None = None
    participants: list[ParticipantInfo] = []
    discussion_points: list[DiscussionPoint] = []
    risks_and_concerns: list[RiskConcern] = []
    follow_ups: list[FollowUp] = []
    parking_lot: list[str] = []
    meeting_effectiveness: MeetingEffectiveness | None = None


class QualityIssue(BaseModel):
    check: str
    severity: str  # "warning" | "error"
    message: str
    details: dict = Field(default_factory=dict)


class QualityReport(BaseModel):
    passed: bool
    score: float  # 0.0 – 1.0
    issues: list[QualityIssue] = []
    speaker_coverage: float = 0.0
    length_ratio: float = 0.0
    hallucination_flags: list[str] = []


class LLMResponse(BaseModel):
    text: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    processing_time_seconds: float
    structured_data: dict | None = None


class PromptTemplate(BaseModel):
    name: str
    meeting_type: str
    system_prompt: str
    user_prompt_template: str


class MeetingContext(BaseModel):
    meeting_id: str
    title: str
    date: str
    duration: str
    attendees: list[str]
    organizer: str | None = None
    meeting_type: str = "other"


class MinutesData(BaseModel):
    """Aggregated data passed to StorageEngine."""

    minutes_json: MinutesJSON
    transcript_json: TranscriptJSON | None = None
    json_path: str
    md_path: str
