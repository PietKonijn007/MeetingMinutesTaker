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
    detailed_notes: str | None = None
    markdown_content: str | None = None
    generated_at: str | None = None
    llm_model: str | None = None
    review_status: str | None = None
    sentiment: str | None = None
    discussion_points: list[dict[str, Any]] = []
    risks_and_concerns: list[dict[str, Any]] = []
    follow_ups: list[dict[str, Any]] = []
    parking_lot: list[str] = []
    key_topics: list[str] = []
    sections: list[dict[str, Any]] = []  # fallback for text+regex path or older meetings


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
    effectiveness_score: int = 0  # 0-5, 0 = unknown


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
    participant_sentiments: dict[str, str] = {}  # name -> sentiment
    effectiveness_score: int = 0  # 0-5, 0 = unknown
    # Post-hoc external notes (pasted from Teams / Zoom / Meet / Otter / etc).
    # Round-tripped so the "External notes" tab can preload the last paste.
    external_notes: str | None = None
    # One of: "processing" | "ready" | "error" | null. When "processing" the
    # background rename + reprocess job is still running and the UI should
    # keep polling.
    external_notes_status: str | None = None
    external_notes_error: str | None = None
    # Status of the post-hoc meeting-type-change job (see
    # ``POST /meetings/{id}/meeting-type``). Same lifecycle as
    # ``external_notes_status``: "processing" → "ready" | "error" | null.
    meeting_type_status: str | None = None
    meeting_type_error: str | None = None


class MeetingUpdate(BaseModel):
    status: str | None = None
    tags: list[str] | None = None


class ExportRequest(BaseModel):
    format: str  # "pdf" | "md"


class ExternalNotesRequest(BaseModel):
    """Request body for ``POST /meetings/{id}/external-notes``.

    The pasted notes are stored verbatim in the meeting's markdown under a
    ``## External notes`` section, and also used to re-run speaker
    identification + summary generation. Freeform text — no parsing.
    """
    text: str


class MeetingTypeChangeRequest(BaseModel):
    """Request body for ``POST /meetings/{id}/meeting-type``.

    Triggers a full reprocess of the meeting against the new type's template.
    Validation of the value happens server-side against ``MeetingType``.
    """
    meeting_type: str


# ---------------------------------------------------------------------------
# Transcript
# ---------------------------------------------------------------------------


class TranscriptSegmentResponse(BaseModel):
    id: int
    start: float | None = None
    end: float | None = None
    speaker: str | None = None
    text: str
    # Alias fields for frontend compat
    start_time: float | None = None
    end_time: float | None = None


class TranscriptResponse(BaseModel):
    meeting_id: str
    full_text: str | None = None
    language: str | None = None
    audio_file_path: str | None = None
    segments: list[TranscriptSegmentResponse] = []
    speakers: list[dict[str, Any]] = []


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
# Talk-Time Analytics
# ---------------------------------------------------------------------------


class MonologueResponse(BaseModel):
    start: float
    end: float
    duration_seconds: float


class SpeakerAnalyticsResponse(BaseModel):
    speaker: str
    talk_time_seconds: float
    talk_time_percentage: float
    segment_count: int
    question_count: int
    monologues: list[MonologueResponse] = []


class TalkTimeAnalyticsResponse(BaseModel):
    total_duration_seconds: float
    speakers: list[SpeakerAnalyticsResponse]
    has_diarization: bool


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------


class RecordingStartRequest(BaseModel):
    audio_device: str | None = None  # device name, or null for config default
    language: str | None = None  # ISO code ("en", "nl", "fr") or null for auto-detect
    planned_minutes: int | None = None  # used by DSK-1 preflight/watchdog


class RecordingStopRequest(BaseModel):
    notes: str | None = None          # User's meeting notes (markdown)
    speakers: str | None = None       # Comma-separated speaker names
    instructions: str | None = None   # Custom instructions for the LLM
    # User-picked template type. When set (non-empty), the pipeline uses this
    # as `PromptRouter.select_template(user_override=...)` and skips the LLM
    # classifier entirely. Valid values: any MeetingType enum value or any
    # template stem present in templates/ (custom templates are supported).
    # Leave null/empty to let the auto-classifier pick the type.
    meeting_type: str | None = None


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
