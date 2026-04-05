"""Tests for MinutesIngester."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from meeting_minutes.models import LLMUsage, MinutesJSON, MinutesMetadata, MinutesSection, ActionItem, Decision
from meeting_minutes.system3.ingest import MinutesIngester
from meeting_minutes.system3.search import SearchEngine
from meeting_minutes.system3.storage import StorageEngine


def _write_minutes_json(path: Path, meeting_id: str | None = None) -> MinutesJSON:
    mid = meeting_id or str(uuid.uuid4())
    minutes = MinutesJSON(
        meeting_id=mid,
        generated_at=datetime.now(timezone.utc),
        meeting_type="standup",
        metadata=MinutesMetadata(
            title="Test Standup",
            date="2025-01-10",
            duration="15 minutes",
            attendees=["Alice", "Bob"],
        ),
        summary="A test standup meeting.",
        sections=[],
        action_items=[ActionItem(description="Test action")],
        decisions=[Decision(description="Test decision")],
        key_topics=["testing"],
        minutes_markdown="# Test Standup\n\nA test standup meeting.",
        llm=LLMUsage(
            provider="anthropic",
            model="claude-sonnet-4-6",
            tokens_used={"input": 100, "output": 50},
            cost_usd=0.001,
            processing_time_seconds=1.0,
        ),
    )
    path.write_text(minutes.model_dump_json())
    return minutes


def test_ingest_creates_meeting_record(db_session, tmp_path):
    """Ingesting a minutes file creates a meeting in the database."""
    storage = StorageEngine(db_session)
    search = SearchEngine(db_session)
    ingester = MinutesIngester(storage, search)

    minutes_path = tmp_path / "test.json"
    minutes = _write_minutes_json(minutes_path)

    result_id = ingester.ingest(minutes_path)
    assert result_id == minutes.meeting_id

    retrieved = storage.get_meeting(minutes.meeting_id)
    assert retrieved is not None
    assert retrieved.title == "Test Standup"


def test_ingest_missing_file_raises(db_session):
    """Ingesting a missing file raises FileNotFoundError."""
    storage = StorageEngine(db_session)
    search = SearchEngine(db_session)
    ingester = MinutesIngester(storage, search)

    with pytest.raises(FileNotFoundError):
        ingester.ingest(Path("/nonexistent/minutes.json"))


def test_ingest_invalid_json_raises(db_session, tmp_path):
    """Ingesting invalid JSON raises ValueError."""
    storage = StorageEngine(db_session)
    search = SearchEngine(db_session)
    ingester = MinutesIngester(storage, search)

    bad_path = tmp_path / "bad.json"
    bad_path.write_text('{"not_valid": true}')

    with pytest.raises((ValueError, Exception)):
        ingester.ingest(bad_path)


def test_ingest_idempotent(db_session, tmp_path):
    """Ingesting the same file twice is idempotent."""
    storage = StorageEngine(db_session)
    search = SearchEngine(db_session)
    ingester = MinutesIngester(storage, search)

    minutes_path = tmp_path / "test.json"
    minutes = _write_minutes_json(minutes_path)

    ingester.ingest(minutes_path)
    ingester.ingest(minutes_path)  # Second time

    meetings = storage.list_meetings(limit=100)
    matching = [m for m in meetings if m.meeting_id == minutes.meeting_id]
    assert len(matching) == 1


def test_ingest_makes_meeting_searchable(db_session, tmp_path):
    """Ingested meeting is searchable."""
    storage = StorageEngine(db_session)
    search = SearchEngine(db_session)
    ingester = MinutesIngester(storage, search)

    unique = f"uniqueterm{uuid.uuid4().hex[:6]}"
    minutes_path = tmp_path / "test.json"
    mid = str(uuid.uuid4())
    minutes = MinutesJSON(
        meeting_id=mid,
        generated_at=datetime.now(timezone.utc),
        meeting_type="other",
        metadata=MinutesMetadata(
            title=f"Meeting about {unique}",
            date="2025-01-10",
            duration="15 minutes",
            attendees=[],
        ),
        summary=f"This meeting discussed {unique}.",
        sections=[],
        action_items=[],
        decisions=[],
        key_topics=[unique],
        minutes_markdown=f"# Meeting\n\nThis meeting discussed {unique}.",
        llm=LLMUsage(
            provider="anthropic",
            model="claude-sonnet-4-6",
            tokens_used={"input": 100, "output": 50},
            cost_usd=0.001,
            processing_time_seconds=1.0,
        ),
    )
    minutes_path.write_text(minutes.model_dump_json())

    ingester.ingest(minutes_path)

    from meeting_minutes.models import SearchQuery
    query = SearchQuery(raw_query=unique, fts_query=unique)
    results = search.search(query)

    ids = [r.meeting_id for r in results.results]
    assert mid in ids
