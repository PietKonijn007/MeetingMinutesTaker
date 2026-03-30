"""Tests for MinutesParser."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from meeting_minutes.models import MeetingContext, ParsedMinutes
from meeting_minutes.system2.parser import MinutesParser


SAMPLE_LLM_RESPONSE = """## Summary
This was a productive standup meeting where the team discussed progress and blockers.

## Alice
- **Done:** Completed API integration
- **Today:** Writing tests
- **Blockers:** None

## Bob
- **Done:** Code review
- **Today:** Deployment
- **Blockers:** Waiting for credentials

## Action Items
- [ ] Deploy to staging — Owner: Bob (Due: 2025-01-15)
- [ ] Update documentation — Owner: Alice

## Decisions
- Use PostgreSQL for production database
- Deploy on Friday

## Key Topics
- API integration
- Deployment
- Testing
"""


def _make_context(meeting_id: str = "test-123") -> MeetingContext:
    return MeetingContext(
        meeting_id=meeting_id,
        title="Daily Standup",
        date="2025-01-10",
        duration="15 minutes",
        attendees=["Alice", "Bob"],
    )


# Feature: meeting-minutes-taker, Property 11: Minutes parser extraction
def test_parser_extracts_all_components():
    """Property 11: Parser extracts summary, sections, action_items, decisions, key_topics."""
    parser = MinutesParser()
    context = _make_context()
    result = parser.parse(SAMPLE_LLM_RESPONSE, context)

    assert isinstance(result, ParsedMinutes)
    assert result.summary
    assert len(result.sections) > 0
    assert len(result.action_items) > 0
    assert len(result.decisions) > 0
    assert len(result.key_topics) > 0


def test_parser_extracts_summary():
    """Parser correctly extracts the summary."""
    parser = MinutesParser()
    result = parser.parse(SAMPLE_LLM_RESPONSE, _make_context())
    assert "productive standup" in result.summary.lower()


def test_parser_extracts_action_items():
    """Parser finds action items with [ ] pattern."""
    parser = MinutesParser()
    result = parser.parse(SAMPLE_LLM_RESPONSE, _make_context())

    assert len(result.action_items) >= 2
    descs = [ai.description for ai in result.action_items]
    assert any("Deploy to staging" in d for d in descs)
    assert any("Update documentation" in d for d in descs)


def test_parser_extracts_action_item_owner():
    """Parser extracts owner from action item."""
    parser = MinutesParser()
    result = parser.parse(SAMPLE_LLM_RESPONSE, _make_context())

    bob_items = [ai for ai in result.action_items if ai.owner == "Bob"]
    assert len(bob_items) > 0


def test_parser_extracts_decisions():
    """Parser extracts decisions from Decisions section."""
    parser = MinutesParser()
    result = parser.parse(SAMPLE_LLM_RESPONSE, _make_context())

    assert len(result.decisions) >= 1
    descs = [d.description for d in result.decisions]
    assert any("PostgreSQL" in d for d in descs)


def test_parser_extracts_key_topics():
    """Parser extracts key topics."""
    parser = MinutesParser()
    result = parser.parse(SAMPLE_LLM_RESPONSE, _make_context())
    assert len(result.key_topics) > 0


def test_parser_handles_empty_response():
    """Parser handles empty LLM response gracefully."""
    parser = MinutesParser()
    result = parser.parse("", _make_context())

    assert isinstance(result, ParsedMinutes)
    assert isinstance(result.summary, str)
    assert isinstance(result.action_items, list)
    assert isinstance(result.decisions, list)


def test_parser_meeting_id_preserved():
    """ParsedMinutes has correct meeting_id."""
    parser = MinutesParser()
    context = _make_context("my-meeting-id")
    result = parser.parse(SAMPLE_LLM_RESPONSE, context)
    assert result.meeting_id == "my-meeting-id"


def test_parser_sections_extracted():
    """Parser extracts all ## sections."""
    parser = MinutesParser()
    result = parser.parse(SAMPLE_LLM_RESPONSE, _make_context())

    headings = [s.heading for s in result.sections]
    assert "Summary" in headings or any("Summary" in h for h in headings)
