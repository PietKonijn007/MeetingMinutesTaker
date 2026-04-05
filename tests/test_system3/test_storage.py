"""Tests for StorageEngine."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from hypothesis import given, settings, HealthCheck

from meeting_minutes.models import (
    ActionItem,
    ActionItemStatus,
    Decision,
    LLMUsage,
    MinutesData,
    MinutesJSON,
    MinutesMetadata,
    MinutesSection,
)
from meeting_minutes.system3.storage import ActionItemFilters, StorageEngine
from tests.strategies import minutes_json_strategy


def _make_minutes_data(
    meeting_id: str | None = None,
    title: str = "Test Meeting",
    attendees: list[str] | None = None,
) -> MinutesData:
    mid = meeting_id or str(uuid.uuid4())
    return MinutesData(
        minutes_json=MinutesJSON(
            meeting_id=mid,
            generated_at=datetime.now(timezone.utc),
            meeting_type="standup",
            metadata=MinutesMetadata(
                title=title,
                date="2025-01-10",
                duration="15 minutes",
                attendees=attendees or ["Alice", "Bob"],
                organizer="Alice",
            ),
            summary="Test summary.",
            sections=[MinutesSection(heading="Discussion", content="Content.")],
            action_items=[
                ActionItem(description="Deploy to staging", owner="Bob"),
            ],
            decisions=[
                Decision(description="Use PostgreSQL"),
            ],
            key_topics=["deployment", "database"],
            minutes_markdown="# Test Meeting\n",
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


# Feature: meeting-minutes-taker, Property 19: Meeting storage round-trip
def test_storage_round_trip(storage):
    """Property 19: Stored meeting can be retrieved with matching fields."""
    data = _make_minutes_data()
    meeting_id = data.minutes_json.meeting_id

    storage.upsert_meeting(data)
    retrieved = storage.get_meeting(meeting_id)

    assert retrieved is not None
    assert retrieved.meeting_id == meeting_id
    assert retrieved.title == "Test Meeting"
    assert retrieved.meeting_type == "standup"
    assert retrieved.minutes is not None
    assert retrieved.minutes.summary == "Test summary."


# Feature: meeting-minutes-taker, Property 20: Attendee person entity creation
def test_attendee_person_creation(storage):
    """Property 20: Meeting is linked to exactly N Person records."""
    attendees = ["Alice", "Bob", "Charlie"]
    data = _make_minutes_data(attendees=attendees)

    storage.upsert_meeting(data)
    retrieved = storage.get_meeting(data.minutes_json.meeting_id)

    assert retrieved is not None
    attendee_names = {a.name for a in retrieved.attendees}
    assert attendee_names == set(attendees)


# Feature: meeting-minutes-taker, Property 21: Storage upsert idempotence
def test_upsert_idempotence(storage):
    """Property 21: Ingesting same meeting twice results in exactly one record."""
    data = _make_minutes_data()
    meeting_id = data.minutes_json.meeting_id

    storage.upsert_meeting(data)
    storage.upsert_meeting(data)  # second time

    # Only one meeting in the database
    meetings = storage.list_meetings(limit=100)
    matching = [m for m in meetings if m.meeting_id == meeting_id]
    assert len(matching) == 1


def test_get_nonexistent_meeting_returns_none(storage):
    """get_meeting returns None for unknown ID."""
    result = storage.get_meeting("nonexistent-id")
    assert result is None


def test_delete_meeting(storage):
    """delete_meeting removes the meeting."""
    data = _make_minutes_data()
    meeting_id = data.minutes_json.meeting_id

    storage.upsert_meeting(data)
    assert storage.get_meeting(meeting_id) is not None

    ok = storage.delete_meeting(meeting_id)
    assert ok
    assert storage.get_meeting(meeting_id) is None


def test_delete_nonexistent_returns_false(storage):
    """delete_meeting returns False for unknown ID."""
    ok = storage.delete_meeting("nonexistent")
    assert not ok


def test_list_meetings_reverse_chronological(storage):
    """list_meetings returns meetings in reverse chronological order."""
    from datetime import timedelta

    base_date = datetime(2025, 1, 1, tzinfo=timezone.utc)
    ids = []
    for i in range(3):
        data = _make_minutes_data()
        data.minutes_json.metadata.date = (base_date + timedelta(days=i)).strftime("%Y-%m-%d")
        storage.upsert_meeting(data)
        ids.append(data.minutes_json.meeting_id)

    meetings = storage.list_meetings(limit=10)
    dates = [m.date for m in meetings if m.meeting_id in ids]
    assert dates == sorted(dates, reverse=True)


def test_get_action_items(storage):
    """get_action_items returns action items."""
    data = _make_minutes_data()
    storage.upsert_meeting(data)

    items = storage.get_action_items()
    assert len(items) >= 1
    assert any(item.description == "Deploy to staging" for item in items)


def test_update_action_item_status(storage):
    """update_action_item_status changes the status."""
    data = _make_minutes_data()
    storage.upsert_meeting(data)

    items = storage.get_action_items()
    assert len(items) > 0
    action_id = items[0].action_item_id

    ok = storage.update_action_item_status(action_id, "done")
    assert ok

    updated = storage.get_action_items(ActionItemFilters(status="done"))
    assert any(item.action_item_id == action_id for item in updated)


def test_update_nonexistent_action_returns_false(storage):
    """update_action_item_status returns False for unknown ID."""
    ok = storage.update_action_item_status("nonexistent", "done")
    assert not ok


# Feature: meeting-minutes-taker, Property 37: Complete meeting deletion
def test_complete_meeting_deletion(storage, search_engine):
    """Property 37: Deleting a meeting removes all associated data."""
    data = _make_minutes_data()
    meeting_id = data.minutes_json.meeting_id

    storage.upsert_meeting(data)

    # Delete
    ok = storage.delete_meeting(meeting_id)
    assert ok

    # Meeting is gone
    assert storage.get_meeting(meeting_id) is None

    # Action items are gone
    items = storage.get_action_items()
    assert all(item.meeting_id != meeting_id for item in items)


@given(minutes=minutes_json_strategy())
@settings(max_examples=50)
def test_any_valid_minutes_can_be_stored(minutes: MinutesJSON):
    """Any valid MinutesJSON can be stored and retrieved."""
    from meeting_minutes.system3.db import get_session_factory
    from meeting_minutes.system3.storage import StorageEngine

    session_factory = get_session_factory("sqlite:///:memory:")
    session = session_factory()
    try:
        storage = StorageEngine(session)
        data = MinutesData(
            minutes_json=minutes,
            json_path=f"/tmp/{minutes.meeting_id}.json",
            md_path=f"/tmp/{minutes.meeting_id}.md",
        )
        storage.upsert_meeting(data)
        retrieved = storage.get_meeting(minutes.meeting_id)
        assert retrieved is not None
        assert retrieved.meeting_id == minutes.meeting_id
    finally:
        session.close()
