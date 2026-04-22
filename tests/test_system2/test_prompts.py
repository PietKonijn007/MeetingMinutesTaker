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


# Regression guard for the live-recording enrichment path. pipeline.py enriches
# `transcript_data.full_text` with an "## Organizer's Meeting Notes" block when
# the user filled the notes textarea during recording, and appends an
# "## Additional Instructions from the Meeting Organizer" block to the system
# prompt when the instructions field is filled. A refactor that silently
# drops either is hard to catch otherwise — both come from the same
# data/notes/<id>.json file and both are easy to accidentally bypass.
def test_user_notes_and_instructions_reach_rendered_prompts():
    from meeting_minutes.config import GenerationConfig
    from meeting_minutes.system2.router import PromptRouter

    templates_dir = Path(__file__).parent.parent.parent / "templates"
    engine = PromptTemplateEngine(templates_dir)
    router = PromptRouter(GenerationConfig(), templates_dir)

    NOTES_SENTINEL = "NOTES_SENTINEL_8M3X_Bob_promised_PR_review"
    INSTR_SENTINEL = "INSTR_SENTINEL_K42Z_focus_on_Q2_budget"
    TRANSCRIPT_BODY = "Alice: let us kick off. Bob: two items on the agenda."

    # Mirror pipeline.py's enhancement logic (lines 712-737). If the real
    # pipeline ever stops injecting notes/instructions, this test needs to
    # fail loud — update it in lockstep with any rewrite of that block.
    enhanced_transcript = (
        f"{TRANSCRIPT_BODY}\n\n"
        f"---\n"
        f"## Organizer's Meeting Notes\n"
        f"[preamble]\n\n"
        f"{NOTES_SENTINEL}"
    )
    custom_system_addendum = (
        f"\n\n## Additional Instructions from the Meeting Organizer\n"
        f"[preamble]\n\n"
        f"{INSTR_SENTINEL}"
    )

    context = MeetingContext(
        meeting_id="notes-roundtrip",
        title="Test",
        date="2026-04-22",
        duration="30 min",
        attendees=["Alice", "Bob"],
        organizer="Alice",
        meeting_type="team_meeting",
    )
    template = router.select_template("team_meeting", 0.95)
    system_prompt, user_prompt = engine.render_structured(
        template, context, enhanced_transcript,
        extra_vars={"vendors": ["AWS", "NetApp"], "length_mode": "concise", "prior_actions": []},
    )
    system_prompt = system_prompt + custom_system_addendum  # pipeline.py line 752-753

    assert NOTES_SENTINEL in user_prompt, (
        "User notes did not reach the rendered user prompt. The pipeline "
        "enriches transcript_text with an 'Organizer's Meeting Notes' block "
        "before rendering; any refactor that bypasses that block silently "
        "loses whatever the user typed into the notes textarea."
    )
    assert INSTR_SENTINEL in system_prompt, (
        "User instructions did not reach the rendered system prompt. The "
        "pipeline appends a 'custom_system_addendum' to the system prompt "
        "after render_structured; any refactor that skips that append "
        "silently loses the steer the user provided."
    )
    assert TRANSCRIPT_BODY in user_prompt, "Transcript body must still be in the user prompt."
