"""Tests for PromptTemplateEngine."""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given, settings

from meeting_minutes.models import MeetingContext, PromptTemplate
from meeting_minutes.system2.prompts import PromptTemplateEngine
from tests.strategies import transcript_json_strategy


# Feature: meeting-minutes-taker, Property 10: Prompt construction completeness
def test_prompt_contains_system_prompt(tmp_path: Path):
    """Property 10: Rendered prompt contains system prompt, context, and transcript."""
    _create_template(tmp_path)
    engine = PromptTemplateEngine(tmp_path)

    template = PromptTemplate(
        name="test.md.j2",
        meeting_type="standup",
        system_prompt="You are a meeting assistant.",
        user_prompt_template="Meeting: {{ title }}\nDate: {{ date }}\nAttendees: {{ attendees | join(', ') }}\n\nTranscript:\n{{ transcript_text }}",
    )

    context = MeetingContext(
        meeting_id="test-123",
        title="Daily Standup",
        date="2025-01-10",
        duration="15 minutes",
        attendees=["Alice", "Bob"],
        meeting_type="standup",
    )

    result = engine.render(template, context, "Hello world transcript text.")

    # Must contain system prompt
    assert "You are a meeting assistant." in result
    # Must contain title
    assert "Daily Standup" in result
    # Must contain date
    assert "2025-01-10" in result
    # Must contain attendees
    assert "Alice" in result
    # Must contain transcript text
    assert "Hello world transcript text." in result


def test_render_with_all_attendees(tmp_path: Path):
    """All attendee names appear in the rendered prompt."""
    engine = PromptTemplateEngine(tmp_path)

    template = PromptTemplate(
        name="test.md.j2",
        meeting_type="other",
        system_prompt="",
        user_prompt_template="{{ attendees | join(', ') }}",
    )

    attendees = ["Alice", "Bob", "Charlie", "Diana"]
    context = MeetingContext(
        meeting_id="test",
        title="Test",
        date="2025-01-10",
        duration="30 min",
        attendees=attendees,
    )

    result = engine.render(template, context, "transcript")

    for name in attendees:
        assert name in result


def test_render_fallback_on_template_error(tmp_path: Path):
    """Render returns something even if template has errors."""
    engine = PromptTemplateEngine(tmp_path)

    # Template with undefined variable
    template = PromptTemplate(
        name="test.md.j2",
        meeting_type="other",
        system_prompt="System.",
        user_prompt_template="{{ undefined_variable }}",
    )

    context = MeetingContext(
        meeting_id="test",
        title="Test",
        date="2025-01-10",
        duration="30 min",
        attendees=[],
    )

    # Should not raise, should return something
    result = engine.render(template, context, "transcript text")
    assert isinstance(result, str)


def _create_template(tmp_path: Path):
    (tmp_path / "test.md.j2").write_text(
        "System prompt.\n\n---\nUser: {{ title }}"
    )
