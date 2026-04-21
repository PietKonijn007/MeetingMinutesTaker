"""Shared Pydantic data models for the Meeting Minutes Taker application."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class MeetingType(str, Enum):
    STANDUP = "standup"
    # Kept for backwards compatibility with pre-existing meetings; new meetings
    # prefer the three perspective-aware variants below.
    ONE_ON_ONE = "one_on_one"
    ONE_ON_ONE_DIRECT_REPORT = "one_on_one_direct_report"
    ONE_ON_ONE_LEADER = "one_on_one_leader"
    ONE_ON_ONE_PEER = "one_on_one_peer"
    TEAM_MEETING = "team_meeting"
    LEADERSHIP_MEETING = "leadership_meeting"
    DECISION_MEETING = "decision_meeting"
    ARCHITECTURE_REVIEW = "architecture_review"
    INCIDENT_REVIEW = "incident_review"
    CUSTOMER_MEETING = "customer_meeting"
    VENDOR_MEETING = "vendor_meeting"
    BOARD_MEETING = "board_meeting"
    INTERVIEW_DEBRIEF = "interview_debrief"
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
    # SPK-1: speaker centroid match, when available.
    suggested_person_id: str | None = None
    suggested_name: str | None = None
    suggestion_score: float = 0.0
    suggestion_tier: str | None = None  # 'high' | 'medium' | 'unknown'


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


class OpenQuestion(BaseModel):
    """A question raised in the meeting that was not answered before it ended."""
    question: str
    raised_by: str | None = None
    owner: str | None = None  # who is expected to answer


class EmailDraft(BaseModel):
    """Follow-up email draft the organizer can send to attendees."""
    subject: str = ""
    to: list[str] = []
    cc: list[str] = []
    body: str = ""


class PriorActionUpdate(BaseModel):
    """An update to a prior meeting's action item acknowledged in this meeting.

    Only populated when the LLM was given prior open action items as input and
    the conversation explicitly acknowledged one as closed, progressed, or
    dropped.
    """
    action_item_id: str
    new_status: str  # "done" | "in_progress" | "cancelled"
    evidence: str | None = None  # short quote or paraphrase from transcript


class StructuredMinutesResponse(BaseModel):
    """The complete schema the LLM fills via tool_use."""
    title: str = ""
    tldr: str = ""  # ~100 words, executive-first summary
    summary: str = ""
    detailed_notes: str = ""
    meeting_type_suggestion: str | None = None
    # auto | public | internal | confidential | restricted
    confidentiality: str | None = None
    sentiment: str | None = None  # constructive, positive, neutral, tense, negative
    participants: list[ParticipantInfo] = []
    discussion_points: list[DiscussionPoint] = []
    decisions: list[StructuredDecision] = []
    action_items: list[StructuredActionItem] = []
    risks_and_concerns: list[RiskConcern] = []
    open_questions: list[OpenQuestion] = []
    follow_ups: list[FollowUp] = []
    key_topics: list[str] = []
    parking_lot: list[str] = []
    prior_action_updates: list[PriorActionUpdate] = []
    email_draft: EmailDraft | None = None
    meeting_effectiveness: MeetingEffectiveness | None = None


class MinutesJSON(BaseModel):
    schema_version: str = "1.0"
    meeting_id: str
    minutes_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    generated_at: datetime
    meeting_type: str
    metadata: MinutesMetadata
    summary: str
    tldr: str = ""
    detailed_notes: str = ""
    sections: list[MinutesSection]
    action_items: list[ActionItem]
    decisions: list[Decision]
    key_topics: list[str]
    minutes_markdown: str
    llm: LLMUsage
    confidentiality: str | None = None
    sentiment: str | None = None
    participants: list[ParticipantInfo] = []
    discussion_points: list[DiscussionPoint] = []
    risks_and_concerns: list[RiskConcern] = []
    open_questions: list[OpenQuestion] = []
    follow_ups: list[FollowUp] = []
    parking_lot: list[str] = []
    prior_action_updates: list[PriorActionUpdate] = []
    email_draft: EmailDraft | None = None
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
    tldr: str = ""
    summary: str
    detailed_notes: str = ""
    sections: list[MinutesSection]
    action_items: list[ActionItem]
    decisions: list[Decision]
    key_topics: list[str]
    raw_llm_response: str
    meeting_context: dict = Field(default_factory=dict)
    confidentiality: str | None = None
    sentiment: str | None = None
    participants: list[ParticipantInfo] = []
    discussion_points: list[DiscussionPoint] = []
    risks_and_concerns: list[RiskConcern] = []
    open_questions: list[OpenQuestion] = []
    follow_ups: list[FollowUp] = []
    parking_lot: list[str] = []
    prior_action_updates: list[PriorActionUpdate] = []
    email_draft: EmailDraft | None = None
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
