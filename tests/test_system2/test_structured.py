"""Tests for structured JSON output: schema, adapter, and round-trip."""

from __future__ import annotations

import json

import pytest

from meeting_minutes.models import (
    ActionItemStatus,
    DiscussionPoint,
    FollowUp,
    MeetingContext,
    MeetingEffectiveness,
    ParticipantInfo,
    RiskConcern,
    StructuredActionItem,
    StructuredDecision,
    StructuredMinutesResponse,
)
from meeting_minutes.system2.parser import StructuredMinutesAdapter
from meeting_minutes.system2.schema import get_minutes_tool_schema, get_tool_definition


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


class TestGetToolDefinition:
    def test_returns_dict_with_required_keys(self):
        defn = get_tool_definition()
        assert isinstance(defn, dict)
        assert "name" in defn
        assert "description" in defn
        assert "input_schema" in defn

    def test_tool_name(self):
        defn = get_tool_definition()
        assert defn["name"] == "record_meeting_minutes"

    def test_input_schema_is_valid_json_schema(self):
        schema = get_minutes_tool_schema()
        assert "type" in schema
        assert schema["type"] == "object"
        assert "properties" in schema

    def test_schema_has_expected_fields(self):
        schema = get_minutes_tool_schema()
        props = schema["properties"]
        expected = [
            "title", "summary", "sentiment", "participants",
            "discussion_points", "decisions", "action_items",
            "risks_and_concerns", "follow_ups", "key_topics",
            "parking_lot", "meeting_effectiveness",
        ]
        for field in expected:
            assert field in props, f"Missing field: {field}"

    def test_no_dollar_ref_remains(self):
        """Ensure _inline_refs removed all $ref entries."""
        schema = get_minutes_tool_schema()
        schema_str = json.dumps(schema)
        assert "$ref" not in schema_str
        assert "$defs" not in schema_str


# ---------------------------------------------------------------------------
# Adapter tests
# ---------------------------------------------------------------------------


def _make_structured_response(**overrides) -> StructuredMinutesResponse:
    defaults = dict(
        title="Q1 Planning Review",
        summary="The team reviewed Q1 goals and planned Q2.",
        meeting_type_suggestion="planning",
        sentiment="constructive",
        participants=[
            ParticipantInfo(name="Alice", role="facilitator"),
            ParticipantInfo(name="Bob", role="contributor"),
        ],
        discussion_points=[
            DiscussionPoint(
                topic="Q1 Review",
                summary="Reviewed Q1 targets; 80% achieved.",
                participants=["Alice", "Bob"],
                sentiment="positive",
                transcript_segment_ids=[0, 1],
            ),
        ],
        decisions=[
            StructuredDecision(
                description="Adopt new CI pipeline",
                made_by="Alice",
                rationale="Faster feedback loops",
                confidence="high",
                transcript_segment_ids=[2],
            ),
        ],
        action_items=[
            StructuredActionItem(
                description="Set up CI pipeline",
                owner="Bob",
                due_date="2026-04-15",
                priority="high",
                transcript_segment_ids=[3],
            ),
        ],
        risks_and_concerns=[
            RiskConcern(description="Budget may be tight", raised_by="Alice"),
        ],
        follow_ups=[
            FollowUp(description="Review CI progress", owner="Bob", timeframe="next week"),
        ],
        key_topics=["Q1 review", "CI pipeline", "Q2 planning"],
        parking_lot=["Discuss hiring plan"],
        meeting_effectiveness=MeetingEffectiveness(
            had_clear_agenda=True,
            decisions_made=1,
            action_items_assigned=1,
            unresolved_items=0,
        ),
    )
    defaults.update(overrides)
    return StructuredMinutesResponse(**defaults)


def _make_context(**overrides) -> MeetingContext:
    defaults = dict(
        meeting_id="test-meeting-123",
        title="Meeting test-meet",
        date="2026-03-30",
        duration="45 minutes",
        attendees=["Alice", "Bob"],
        meeting_type="planning",
    )
    defaults.update(overrides)
    return MeetingContext(**defaults)


class TestStructuredMinutesAdapter:
    def test_adapt_maps_title(self):
        adapter = StructuredMinutesAdapter()
        result = adapter.adapt(_make_structured_response(), _make_context())
        assert result.title == "Q1 Planning Review"

    def test_adapt_maps_summary(self):
        adapter = StructuredMinutesAdapter()
        result = adapter.adapt(_make_structured_response(), _make_context())
        assert "Q1 goals" in result.summary

    def test_adapt_maps_meeting_id_from_context(self):
        adapter = StructuredMinutesAdapter()
        ctx = _make_context(meeting_id="my-id-456")
        result = adapter.adapt(_make_structured_response(), ctx)
        assert result.meeting_id == "my-id-456"

    def test_adapt_maps_discussion_points_to_sections(self):
        adapter = StructuredMinutesAdapter()
        result = adapter.adapt(_make_structured_response(), _make_context())
        assert len(result.sections) == 1
        assert result.sections[0].heading == "Q1 Review"
        assert result.sections[0].type == "discussion"

    def test_adapt_maps_action_items(self):
        adapter = StructuredMinutesAdapter()
        result = adapter.adapt(_make_structured_response(), _make_context())
        assert len(result.action_items) == 1
        ai = result.action_items[0]
        assert ai.description == "Set up CI pipeline"
        assert ai.owner == "Bob"
        assert ai.due_date == "2026-04-15"
        assert ai.priority == "high"
        assert ai.status == ActionItemStatus.OPEN
        assert ai.transcript_segment_ids == [3]

    def test_adapt_maps_decisions(self):
        adapter = StructuredMinutesAdapter()
        result = adapter.adapt(_make_structured_response(), _make_context())
        assert len(result.decisions) == 1
        d = result.decisions[0]
        assert d.description == "Adopt new CI pipeline"
        assert d.rationale == "Faster feedback loops"
        assert d.confidence == "high"

    def test_adapt_maps_key_topics(self):
        adapter = StructuredMinutesAdapter()
        result = adapter.adapt(_make_structured_response(), _make_context())
        assert "CI pipeline" in result.key_topics

    def test_adapt_maps_sentiment(self):
        adapter = StructuredMinutesAdapter()
        result = adapter.adapt(_make_structured_response(), _make_context())
        assert result.sentiment == "constructive"

    def test_adapt_maps_participants(self):
        adapter = StructuredMinutesAdapter()
        result = adapter.adapt(_make_structured_response(), _make_context())
        assert len(result.participants) == 2
        assert result.participants[0].name == "Alice"

    def test_adapt_maps_risks(self):
        adapter = StructuredMinutesAdapter()
        result = adapter.adapt(_make_structured_response(), _make_context())
        assert len(result.risks_and_concerns) == 1
        assert "Budget" in result.risks_and_concerns[0].description

    def test_adapt_maps_follow_ups(self):
        adapter = StructuredMinutesAdapter()
        result = adapter.adapt(_make_structured_response(), _make_context())
        assert len(result.follow_ups) == 1

    def test_adapt_maps_parking_lot(self):
        adapter = StructuredMinutesAdapter()
        result = adapter.adapt(_make_structured_response(), _make_context())
        assert result.parking_lot == ["Discuss hiring plan"]

    def test_adapt_maps_meeting_effectiveness(self):
        adapter = StructuredMinutesAdapter()
        result = adapter.adapt(_make_structured_response(), _make_context())
        assert result.meeting_effectiveness is not None
        assert result.meeting_effectiveness.had_clear_agenda is True
        assert result.meeting_effectiveness.decisions_made == 1

    def test_adapt_with_empty_structured_response(self):
        adapter = StructuredMinutesAdapter()
        empty = StructuredMinutesResponse(summary="Minimal meeting.")
        result = adapter.adapt(empty, _make_context())
        assert result.summary == "Minimal meeting."
        assert result.action_items == []
        assert result.decisions == []
        assert result.discussion_points == []

    def test_round_trip_raw_llm_response(self):
        """Ensure raw_llm_response is valid JSON that round-trips."""
        adapter = StructuredMinutesAdapter()
        structured = _make_structured_response()
        result = adapter.adapt(structured, _make_context())
        # raw_llm_response should be parseable back to StructuredMinutesResponse
        round_tripped = StructuredMinutesResponse.model_validate_json(result.raw_llm_response)
        assert round_tripped.title == structured.title
        assert len(round_tripped.action_items) == len(structured.action_items)
