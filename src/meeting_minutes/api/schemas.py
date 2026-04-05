"""Pydantic response models for the REST API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------


class PersonResponse(BaseModel):
    person_id: str
    name: str
    email: str | None = None


class PersonDetailResponse(PersonResponse):
    meeting_count: int = 0
    open_action_count: int = 0
    last_meeting_date: str | None = None


# ---------------------------------------------------------------------------
# Action items
# ---------------------------------------------------------------------------


class ActionItemResponse(BaseModel):
    action_item_id: str
    description: str
    owner: str | None = None
    due_date: str | None = None
    status: str
    meeting_id: str | None = None
    meeting_title: str | None = None


class ActionItemUpdate(BaseModel):
    status: str


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


class DecisionResponse(BaseModel):
    decision_id: str
    description: str
    made_by: str | None = None
    mentioned_at_seconds: float | None = None
    meeting_id: str | None = None
    meeting_title: str | None = None
    meeting_date: str | None = None


# ---------------------------------------------------------------------------
# Minutes
# ---------------------------------------------------------------------------


class MinutesResponse(BaseModel):
    minutes_id: str | None = None
    summary: str | None = None
    markdown_content: str | None = None
    generated_at: str | None = None
    llm_model: str | None = None
    review_status: str | None = None


# ---------------------------------------------------------------------------
# Meetings
# ---------------------------------------------------------------------------


class MeetingListItem(BaseModel):
    meeting_id: str
    title: str | None = None
    date: str | None = None
    meeting_type: str | None = None
    duration: str | None = None
    organizer: str | None = None
    summary: str | None = None
    attendee_names: list[str] = []
    action_item_count: int = 0
    decision_count: int = 0


class MeetingDetail(BaseModel):
    meeting_id: str
    title: str | None = None
    date: str | None = None
    meeting_type: str | None = None
    duration: str | None = None
    organizer: str | None = None
    status: str | None = None
    attendees: list[PersonResponse] = []
    minutes: MinutesResponse | None = None
    action_items: list[ActionItemResponse] = []
    decisions: list[DecisionResponse] = []
    transcript_text: str | None = None
    audio_file_path: str | None = None


class MeetingUpdate(BaseModel):
    status: str | None = None
    tags: list[str] | None = None


class ExportRequest(BaseModel):
    format: str  # "pdf" | "md"


# ---------------------------------------------------------------------------
# Transcript
# ---------------------------------------------------------------------------


class TranscriptResponse(BaseModel):
    meeting_id: str
    full_text: str | None = None
    language: str | None = None
    audio_file_path: str | None = None


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


class SearchResultItem(BaseModel):
    meeting_id: str
    title: str
    date: str | None = None
    meeting_type: str
    snippet: str


# ---------------------------------------------------------------------------
# Pagination wrapper
# ---------------------------------------------------------------------------


class PaginatedResponse(BaseModel):
    items: list[Any]
    total: int
    limit: int
    offset: int


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


class StatsOverview(BaseModel):
    total_meetings: int
    meetings_this_week: int
    open_actions: int
    avg_duration_minutes: float | None = None


class WeeklyCount(BaseModel):
    week: str  # ISO week start date
    count: int


class TypeDistribution(BaseModel):
    meeting_type: str
    count: int


class ActionVelocityWeek(BaseModel):
    week: str
    created: int
    completed: int


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------


class RecordingStartRequest(BaseModel):
    audio_device: str | None = None  # device name, or null for config default
    language: str | None = None  # ISO code ("en", "nl", "fr") or null for auto-detect


class RecordingStopRequest(BaseModel):
    notes: str | None = None          # User's meeting notes (markdown)
    speakers: str | None = None       # Comma-separated speaker names
    instructions: str | None = None   # Custom instructions for the LLM


class RecordingStartResponse(BaseModel):
    meeting_id: str
    status: str = "recording"


class RecordingStatusResponse(BaseModel):
    state: str  # "idle" | "recording"
    meeting_id: str | None = None
    elapsed_seconds: float | None = None
    audio_level: float = 0.0  # 0.0–1.0 RMS audio level


class PipelineJobStatus(BaseModel):
    meeting_id: str
    step: str  # "saving" | "transcribing" | "generating" | "indexing" | "done" | "error"
    progress: float  # 0.0–1.0
    error: str | None = None
    started_at: float
    elapsed_seconds: float


class AudioDeviceResponse(BaseModel):
    index: int
    name: str
    max_input_channels: int
    max_output_channels: int
    default_sample_rate: float
    type: str  # "input" | "output" | "input/output"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class ConfigResponse(BaseModel):
    """Entire config as a dict."""
    config: dict[str, Any]


class ConfigUpdate(BaseModel):
    """Partial config update (merge semantics)."""
    config: dict[str, Any]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ErrorResponse(BaseModel):
    error: str
    detail: str | None = None
