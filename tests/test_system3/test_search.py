"""Tests for SearchEngine."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from meeting_minutes.models import MinutesData, MinutesJSON, MinutesMetadata, MinutesSection, LLMUsage, ActionItem, Decision
from meeting_minutes.system3.search import SearchEngine
from meeting_minutes.system3.storage import StorageEngine
from meeting_minutes.models import SearchQuery


def _store_meeting(
    storage: StorageEngine,
    search: SearchEngine,
    meeting_id: str | None = None,
    title: str = "Test Meeting",
    meeting_type: str = "standup",
    transcript_text: str = "",
    minutes_text: str = "",
    date: str = "2025-01-10",
    attendees: list[str] | None = None,
) -> str:
    mid = meeting_id or str(uuid.uuid4())
    data = MinutesData(
        minutes_json=MinutesJSON(
            meeting_id=mid,
            generated_at=datetime.now(timezone.utc),
            meeting_type=meeting_type,
            metadata=MinutesMetadata(
                title=title,
                date=date,
                duration="15 minutes",
                attendees=attendees or [],
            ),
            summary=minutes_text or "Summary of the meeting.",
            sections=[],
            action_items=[],
            decisions=[],
            key_topics=[],
            minutes_markdown=minutes_text or f"# {title}\n",
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
        transcript_json=None,
    )
    # Manually set transcript text
    if transcript_text:
        from meeting_minutes.system3.db import TranscriptORM
        storage.upsert_meeting(data)
        # Update transcript in DB
        from sqlalchemy import text
        storage._session.execute(
            text("UPDATE transcripts SET full_text = :tt WHERE meeting_id = :mid"),
            {"tt": transcript_text, "mid": mid},
        )
        storage._session.commit()
        # Reindex
        search.reindex_meeting(mid)
    else:
        storage.upsert_meeting(data)
    return mid


def test_search_by_title(storage, search_engine, db_session):
    """Search finds meeting by title keyword."""
    mid = _store_meeting(storage, search_engine, title="Quarterly Planning Session", minutes_text="quarterly planning discussion")
    search_engine.reindex_meeting(mid)

    query = SearchQuery(raw_query="planning", fts_query="planning")
    results = search_engine.search(query)

    ids = [r.meeting_id for r in results.results]
    assert mid in ids


# Feature: meeting-minutes-taker, Property 22: FTS index sync after storage
def test_fts_index_sync(storage, search_engine):
    """Property 22: Stored meeting is searchable by unique term."""
    unique_term = f"xyzunique{uuid.uuid4().hex[:8]}"
    mid = _store_meeting(
        storage, search_engine,
        minutes_text=f"Meeting about {unique_term} integration.",
    )
    search_engine.reindex_meeting(mid)

    query = SearchQuery(raw_query=unique_term, fts_query=unique_term)
    results = search_engine.search(query)

    ids = [r.meeting_id for r in results.results]
    assert mid in ids


# Feature: meeting-minutes-taker, Property 26: Meeting type filter
def test_meeting_type_filter(storage, search_engine):
    """Property 26: Type filter returns only matching meeting types."""
    mid_standup = _store_meeting(storage, search_engine, meeting_type="standup", title="Standup")
    mid_planning = _store_meeting(storage, search_engine, meeting_type="planning", title="Planning")

    query = SearchQuery(raw_query="", fts_query="", meeting_type="standup", limit=50)
    results = search_engine.search(query)

    ids = [r.meeting_id for r in results.results]
    assert mid_standup in ids
    # Planning meeting should NOT appear
    assert mid_planning not in ids


# Feature: meeting-minutes-taker, Property 25: Date range filter
def test_date_range_filter(storage, search_engine):
    """Property 25: Date range filter returns only meetings in range."""
    mid_old = _store_meeting(storage, search_engine, date="2024-01-01", title="Old Meeting")
    mid_new = _store_meeting(storage, search_engine, date="2025-06-01", title="New Meeting")

    after = datetime(2025, 1, 1, tzinfo=timezone.utc)
    query = SearchQuery(raw_query="", fts_query="", after_date=after, limit=50)
    results = search_engine.search(query)

    ids = [r.meeting_id for r in results.results]
    assert mid_new in ids
    assert mid_old not in ids


def test_search_empty_query_returns_all(storage, search_engine):
    """Empty FTS query with no filters returns all meetings."""
    mid1 = _store_meeting(storage, search_engine, title="Meeting A")
    mid2 = _store_meeting(storage, search_engine, title="Meeting B")

    query = SearchQuery(raw_query="", fts_query="", limit=50)
    results = search_engine.search(query)

    ids = [r.meeting_id for r in results.results]
    assert mid1 in ids
    assert mid2 in ids


def test_parse_query_extracts_type(search_engine):
    """parse_query extracts type: filter."""
    query = search_engine.parse_query("type:standup team sync")
    assert query.meeting_type == "standup"
    assert "standup" not in query.fts_query
    assert "team sync" in query.fts_query


def test_parse_query_extracts_dates(search_engine):
    """parse_query extracts after: and before: filters."""
    query = search_engine.parse_query("meeting after:2025-01-01 before:2025-12-31")
    assert query.after_date is not None
    assert query.after_date.year == 2025
    assert query.before_date is not None
    assert query.before_date.year == 2025
    assert "after:2025-01-01" not in query.fts_query


def test_remove_from_index(storage, search_engine):
    """remove_from_index removes meeting from FTS."""
    unique_term = f"removetest{uuid.uuid4().hex[:8]}"
    mid = _store_meeting(storage, search_engine, minutes_text=f"Content with {unique_term}")
    search_engine.reindex_meeting(mid)

    # Verify it's indexed
    query = SearchQuery(raw_query=unique_term, fts_query=unique_term)
    results_before = search_engine.search(query)
    assert mid in [r.meeting_id for r in results_before.results]

    # Remove
    search_engine.remove_from_index(mid)

    # No longer searchable
    results_after = search_engine.search(query)
    assert mid not in [r.meeting_id for r in results_after.results]


# Feature: meeting-minutes-taker, Property 27: BM25 ranking order
def test_search_results_ordered_by_relevance(storage, search_engine):
    """Property 27: More relevant meetings rank higher."""
    term = f"relevance{uuid.uuid4().hex[:6]}"
    # Meeting with term appearing many times
    mid_high = _store_meeting(
        storage, search_engine,
        minutes_text=f"{term} {term} {term} {term} highly relevant meeting",
        title=f"{term} important",
    )
    # Meeting with term appearing once
    mid_low = _store_meeting(
        storage, search_engine,
        minutes_text=f"meeting with {term} once",
        title="Less relevant",
    )

    search_engine.reindex_meeting(mid_high)
    search_engine.reindex_meeting(mid_low)

    query = SearchQuery(raw_query=term, fts_query=term, limit=10)
    results = search_engine.search(query)

    # Both should appear
    ids = [r.meeting_id for r in results.results]
    assert mid_high in ids
    assert mid_low in ids


# Feature: meeting-minutes-taker, Property 23: FTS phrase matching
def test_fts_phrase_matching(storage, search_engine):
    """Property 23: Exact phrase search returns correct meeting."""
    phrase = "quantum entanglement breakthrough"
    mid = _store_meeting(
        storage, search_engine,
        minutes_text=f"Discussion about {phrase} in science.",
    )
    search_engine.reindex_meeting(mid)

    # Exact phrase
    query = SearchQuery(raw_query=f'"{phrase}"', fts_query=f'"{phrase}"')
    results = search_engine.search(query)

    ids = [r.meeting_id for r in results.results]
    assert mid in ids
