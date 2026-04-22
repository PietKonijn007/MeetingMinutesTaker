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


# Regression guard — PR #12 hotfix. The 18 built-in templates call a shared
# Jinja macro `m.meeting_header(...)` that previously referenced
# `{{ transcript_text }}` directly. Because `{% import %}` imports without
# context by default, the macro couldn't see the caller's `transcript_text`
# and the rendered prompt contained `## Transcript\n` with an empty body —
# the LLM was generating minutes from the template structure + user notes
# alone, with no transcript. This test renders every real template and
# verifies the transcript sentinel reaches the final prompt.
def test_every_builtin_template_includes_transcript_text():
    from meeting_minutes.config import GenerationConfig
    from meeting_minutes.system2.router import PromptRouter, _discover_all_types

    templates_dir = Path(__file__).parent.parent.parent / "templates"
    assert templates_dir.is_dir(), templates_dir
    engine = PromptTemplateEngine(templates_dir)
    router = PromptRouter(GenerationConfig(), templates_dir)

    context = MeetingContext(
        meeting_id="abc",
        title="Regression Test",
        date="2026-04-22",
        duration="30 min",
        attendees=["Alice", "Bob"],
        organizer="Alice",
        meeting_type="other",
    )
    sentinel = "TRANSCRIPT_SENTINEL_Q7A3K9_unique_marker"
    extra = {"vendors": ["AWS", "NetApp"], "length_mode": "concise", "prior_actions": []}

    missing = []
    for meeting_type in sorted(_discover_all_types(templates_dir)):
        template = router.select_template(meeting_type, 0.95)
        _sys, user_prompt = engine.render_structured(
            template, context, sentinel, extra_vars=extra,
        )
        if sentinel not in user_prompt:
            missing.append(template.name)

    assert not missing, (
        f"transcript_text did not reach the rendered prompt for: {missing}. "
        "This indicates a regression in shared-macro context handling "
        "(_shared.md.j2 was previously imported without context)."
    )
