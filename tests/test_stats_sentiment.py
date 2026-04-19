"""Tests for ANA-1 Panel 3 — sentiment trends."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from meeting_minutes.stats_analytics import SENTIMENT_SCORES, sentiment_trend
from meeting_minutes.system3.db import (
    MeetingORM,
    MinutesORM,
    PersonORM,
    get_session_factory,
)


@pytest.fixture
def session():
    sf = get_session_factory("sqlite:///:memory:")
    s = sf()
    yield s
    s.close()


def _mk_meeting_with_minutes(
    session,
    *,
    date,
    sentiment=None,
    structured=None,
    meeting_type="standup",
):
    m = MeetingORM(
        meeting_id=f"m-{uuid.uuid4().hex[:8]}",
        title="m",
        date=date,
        meeting_type=meeting_type,
        status="final",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session.add(m)
    session.flush()
    minutes = MinutesORM(
        meeting_id=m.meeting_id,
        minutes_id=f"min-{uuid.uuid4().hex[:8]}",
        markdown_content="",
        summary="",
        generated_at=datetime.now(timezone.utc),
        llm_model="test",
        sentiment=sentiment,
        structured_json=json.dumps(structured) if structured is not None else None,
    )
    session.add(minutes)
    session.commit()
    return m


def test_top_level_sentiment_mapping(session):
    now = datetime.now(timezone.utc)
    _mk_meeting_with_minutes(session, date=now - timedelta(days=10), sentiment="positive")
    _mk_meeting_with_minutes(session, date=now - timedelta(days=5), sentiment="tense")
    _mk_meeting_with_minutes(session, date=now - timedelta(days=2), sentiment="neutral")

    out = sentiment_trend(session, days=30)
    series = out["series"]
    assert len(series) == 3
    # Chronological order.
    assert [s["sentiment"] for s in series] == ["positive", "tense", "neutral"]
    assert [s["sentiment_score"] for s in series] == [
        SENTIMENT_SCORES["positive"],
        SENTIMENT_SCORES["tense"],
        SENTIMENT_SCORES["neutral"],
    ]


def test_per_person_sentiment_from_structured(session):
    now = datetime.now(timezone.utc)
    jon = PersonORM(person_id="p-jon", name="Jon")
    session.add(jon)
    session.commit()

    _mk_meeting_with_minutes(
        session,
        date=now - timedelta(days=5),
        structured={
            "participants": [
                {"name": "Jon", "sentiment": "positive"},
                {"name": "Sarah", "sentiment": "tense"},
            ],
        },
    )
    _mk_meeting_with_minutes(
        session,
        date=now - timedelta(days=2),
        structured={
            "participants": [
                {"name": "Jon", "sentiment": "negative"},
            ],
        },
    )

    out = sentiment_trend(session, days=30, person="p-jon")
    assert len(out["series"]) == 2
    assert [s["sentiment"] for s in out["series"]] == ["positive", "negative"]


def test_sentiment_accepts_raw_name(session):
    now = datetime.now(timezone.utc)
    _mk_meeting_with_minutes(
        session,
        date=now - timedelta(days=2),
        structured={
            "participants": [{"name": "Alice", "sentiment": "constructive"}],
        },
    )
    out = sentiment_trend(session, days=30, person="Alice")
    assert out["series"][0]["sentiment_score"] == SENTIMENT_SCORES["constructive"]


def test_no_meetings_returns_empty(session):
    out = sentiment_trend(session, days=30)
    assert out == {"series": []}
