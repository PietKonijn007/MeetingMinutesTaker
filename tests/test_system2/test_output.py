"""Tests for MinutesJSONWriter."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from hypothesis import given, settings

from meeting_minutes.models import (
    LLMResponse,
    MinutesJSON,
    ParsedMinutes,
    QualityReport,
)
from meeting_minutes.system2.output import MinutesJSONWriter
from tests.strategies import minutes_json_strategy


# Feature: meeting-minutes-taker, Property 12: Minutes output file creation
def test_output_creates_json_and_md(tmp_path: Path, sample_parsed_minutes, sample_llm_response, sample_quality_report):
    """Property 12: Both JSON and Markdown files are created."""
    writer = MinutesJSONWriter()

    json_path, md_path = writer.write(
        minutes=sample_parsed_minutes,
        quality_report=sample_quality_report,
        llm_response=sample_llm_response,
        output_dir=tmp_path,
        meeting_context={
            "title": "Daily Standup",
            "date": "2025-01-10",
            "duration": "15 minutes",
            "attendees": ["Alice", "Bob"],
            "meeting_type": "standup",
        },
    )

    assert json_path.exists()
    assert md_path.exists()


# Feature: meeting-minutes-taker, Property 14: Minutes JSON schema validity
def test_minutes_json_schema_validity(tmp_path: Path, sample_parsed_minutes, sample_llm_response, sample_quality_report):
    """Property 14: Written MinutesJSON has all required fields."""
    writer = MinutesJSONWriter()

    ctx = {
        "title": "Test Meeting",
        "date": "2025-01-10",
        "duration": "30 minutes",
        "attendees": ["Alice"],
        "meeting_type": "other",
    }

    json_path, _ = writer.write(
        minutes=sample_parsed_minutes,
        quality_report=sample_quality_report,
        llm_response=sample_llm_response,
        output_dir=tmp_path,
        meeting_context=ctx,
    )

    data = json.loads(json_path.read_text())

    required_fields = [
        "schema_version", "meeting_id", "minutes_id", "generated_at",
        "meeting_type", "metadata", "summary", "sections", "action_items",
        "decisions", "key_topics", "minutes_markdown", "llm",
    ]
    for field in required_fields:
        assert field in data, f"Missing field: {field}"

    # LLM block
    llm = data["llm"]
    assert "provider" in llm
    assert "model" in llm
    assert "tokens_used" in llm
    assert "cost_usd" in llm
    assert "processing_time_seconds" in llm


# Feature: meeting-minutes-taker, Property 15: Minutes JSON round-trip
@given(minutes=minutes_json_strategy())
@settings(max_examples=100)
def test_minutes_json_round_trip(minutes: MinutesJSON):
    """Property 15: MinutesJSON serializes and deserializes without data loss."""
    serialized = minutes.model_dump_json()
    restored = MinutesJSON.model_validate_json(serialized)

    assert restored.meeting_id == minutes.meeting_id
    assert restored.minutes_id == minutes.minutes_id
    assert restored.meeting_type == minutes.meeting_type
    assert restored.summary == minutes.summary
    assert len(restored.action_items) == len(minutes.action_items)
    assert len(restored.decisions) == len(minutes.decisions)


def test_output_file_named_by_meeting_id(tmp_path: Path, sample_parsed_minutes, sample_llm_response, sample_quality_report):
    """Output files are named {meeting_id}.json and {meeting_id}.md."""
    writer = MinutesJSONWriter()

    json_path, md_path = writer.write(
        minutes=sample_parsed_minutes,
        quality_report=sample_quality_report,
        llm_response=sample_llm_response,
        output_dir=tmp_path,
    )

    assert json_path.stem == sample_parsed_minutes.meeting_id
    assert md_path.stem == sample_parsed_minutes.meeting_id


def test_output_directory_created(sample_parsed_minutes, sample_llm_response, sample_quality_report, tmp_path):
    """Output directory is created if it doesn't exist."""
    writer = MinutesJSONWriter()
    output_dir = tmp_path / "nested" / "dir"

    json_path, md_path = writer.write(
        minutes=sample_parsed_minutes,
        quality_report=sample_quality_report,
        llm_response=sample_llm_response,
        output_dir=output_dir,
    )

    assert output_dir.exists()
    assert json_path.exists()
