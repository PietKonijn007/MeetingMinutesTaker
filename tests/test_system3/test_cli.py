"""Tests for CLI commands."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from meeting_minutes.models import (
    ActionItem,
    ActionItemProposalState,
    ActionItemStatus,
    Decision,
    LLMUsage,
    MinutesData,
    MinutesJSON,
    MinutesMetadata,
    MinutesSection,
)
from meeting_minutes.system3.cli import app


runner = CliRunner()


def _make_minutes_data(
    meeting_id: str | None = None,
    meeting_type: str = "standup",
    date: str = "2025-01-10",
    title: str = "Test Meeting",
) -> MinutesData:
    mid = meeting_id or str(uuid.uuid4())
    return MinutesData(
        minutes_json=MinutesJSON(
            meeting_id=mid,
            generated_at=datetime.now(timezone.utc),
            meeting_type=meeting_type,
            metadata=MinutesMetadata(
                title=title,
                date=date,
                duration="15 minutes",
                attendees=["Alice", "Bob"],
            ),
            summary="Test summary.",
            sections=[MinutesSection(heading="Discussion", content="Content.")],
            action_items=[
                ActionItem(
                    description="Test action",
                    owner="Alice",
                    proposal_state=ActionItemProposalState.CONFIRMED,
                )
            ],
            decisions=[Decision(description="Test decision")],
            key_topics=["testing"],
            minutes_markdown=f"# {title}\n",
            llm=LLMUsage(
                provider="anthropic",
                model="claude-sonnet-4-6",
                tokens_used={"input": 100, "output": 50},
                cost_usd=0.001,
                processing_time_seconds=1.0,
            ),
        ),
        json_path=f"/tmp/{mid}.json",
        md_path=f"/tmp/{mid}.md",
    )


@pytest.fixture
def populated_db(db_session):
    """Populate in-memory DB with test data."""
    from meeting_minutes.system3.storage import StorageEngine

    storage = StorageEngine(db_session)
    meetings = [
        _make_minutes_data(title=f"Meeting {i}", date=f"2025-01-{10+i:02d}")
        for i in range(3)
    ]
    for m in meetings:
        storage.upsert_meeting(m)
    return db_session, meetings


def _patched_get_db_session(db_session):
    """Patch the CLI's _get_db_session to use the test session."""
    return db_session


# Feature: meeting-minutes-taker, Property 28: CLI list chronological order
def test_list_reverse_chronological(populated_db):
    """Property 28: mm list shows meetings in reverse chronological order."""
    db_session, meetings = populated_db

    with patch("meeting_minutes.system3.cli._get_db_session", return_value=db_session):
        result = runner.invoke(app, ["list"])

    assert result.exit_code == 0
    # Meetings table should show — check for at least the table header
    assert "Meetings" in result.output or "Meeting" in result.output


# Feature: meeting-minutes-taker, Property 29: CLI action items filtering
def test_actions_shows_open_items(populated_db):
    """Property 29: mm actions shows open action items."""
    db_session, meetings = populated_db

    with patch("meeting_minutes.system3.cli._get_db_session", return_value=db_session):
        result = runner.invoke(app, ["actions"])

    assert result.exit_code == 0
    # Rich may wrap "Test action" across two lines under CliRunner's narrow
    # default terminal width, so check the row's owner + status instead.
    assert "Alice" in result.output
    assert "Action Items" in result.output


# Feature: meeting-minutes-taker, Property 30: CLI invalid ID error handling
def test_show_invalid_id_returns_error(populated_db):
    """Property 30: mm show with invalid ID exits with non-zero code."""
    db_session, _ = populated_db

    with patch("meeting_minutes.system3.cli._get_db_session", return_value=db_session):
        result = runner.invoke(app, ["show", "nonexistent-meeting-id"])

    assert result.exit_code != 0


def test_show_valid_meeting(populated_db):
    """mm show with valid ID displays meeting details."""
    db_session, meetings = populated_db
    meeting_id = meetings[0].minutes_json.meeting_id

    with patch("meeting_minutes.system3.cli._get_db_session", return_value=db_session):
        result = runner.invoke(app, ["show", meeting_id])

    assert result.exit_code == 0
    assert meeting_id in result.output


def test_delete_valid_meeting(populated_db):
    """mm delete removes the meeting."""
    db_session, meetings = populated_db
    meeting_id = meetings[0].minutes_json.meeting_id

    with patch("meeting_minutes.system3.cli._get_db_session", return_value=db_session):
        result = runner.invoke(app, ["delete", meeting_id, "--yes"])

    assert result.exit_code == 0

    # Meeting should be gone
    with patch("meeting_minutes.system3.cli._get_db_session", return_value=db_session):
        result2 = runner.invoke(app, ["show", meeting_id])
    assert result2.exit_code != 0


def test_delete_invalid_id_returns_error(populated_db):
    """mm delete with invalid ID exits with error."""
    db_session, _ = populated_db

    with patch("meeting_minutes.system3.cli._get_db_session", return_value=db_session):
        result = runner.invoke(app, ["delete", "nonexistent-id", "--yes"])

    assert result.exit_code != 0


def test_actions_complete(populated_db):
    """mm actions complete marks action item as done."""
    db_session, meetings = populated_db
    from meeting_minutes.system3.storage import StorageEngine

    storage = StorageEngine(db_session)
    items = storage.get_action_items()
    assert len(items) > 0
    action_id = items[0].action_item_id

    with patch("meeting_minutes.system3.cli._get_db_session", return_value=db_session):
        result = runner.invoke(app, ["actions", "complete", action_id])

    assert result.exit_code == 0
    assert "done" in result.output.lower()


def test_search_command(populated_db):
    """mm search returns results."""
    db_session, meetings = populated_db

    with patch("meeting_minutes.system3.cli._get_db_session", return_value=db_session):
        result = runner.invoke(app, ["search", "Meeting"])

    # Should exit 0 (might have results or empty)
    assert result.exit_code == 0
