"""Tests for the pre-meeting briefing endpoint (BRF-1)."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from meeting_minutes.api.deps import get_config, get_db_session
from meeting_minutes.api.routes.brief import (
    _build_context_excerpts,
    _build_open_commitments,
    _build_recent_sentiment,
    _build_suggested_start,
    _build_who_and_when_last,
    _meetings_with_any_attendee,
    _resolve_people,
    get_briefing,
    router,
)
from meeting_minutes.config import AppConfig
from meeting_minutes.system3.db import (
    ActionItemORM,
    DecisionORM,
    EmbeddingChunkORM,
    MeetingORM,
    MeetingSeriesMemberORM,
    MeetingSeriesORM,
    MinutesORM,
    PersonORM,
    get_session_factory,
    meeting_attendees,
)
from meeting_minutes.system3.series import compute_attendee_hash


@pytest.fixture
def session_factory():
    """A sessionmaker bound to a single in-memory SQLite that supports
    multi-thread access (required for TestClient, which runs requests in a
    background thread)."""
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from meeting_minutes.system3.db import create_tables

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _load_vec_on_connect(dbapi_conn, connection_record):
        try:
            import sqlite_vec
            dbapi_conn.enable_load_extension(True)
            sqlite_vec.load(dbapi_conn)
        except Exception:
            pass

    create_tables(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.fixture
def session(session_factory):
    s = session_factory()
    yield s
    s.close()


@pytest.fixture
def app(session_factory):
    """A FastAPI app with only the /api/brief router mounted."""
    app = FastAPI()
    app.include_router(router)

    def override_session():
        s = session_factory()
        try:
            yield s
        finally:
            s.close()

    def override_config():
        # The test config does NOT enable summarize_with_llm; the embedding
        # engine fallback short-circuits to newest chunks when vec search
        # fails, so we don't touch the network in tests.
        return AppConfig()

    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_config] = override_config
    return app


@pytest.fixture
def client(app, monkeypatch):
    # The context-excerpt builder instantiates an EmbeddingEngine which
    # loads a sentence-transformer model. Stub it out so tests are fast
    # and offline.
    import meeting_minutes.api.routes.brief as brief_module
    import meeting_minutes.embeddings as emb_module

    class _FakeEngine:
        def __init__(self, *_a, **_kw): ...

        def search(self, *_a, **_kw):
            return []

    monkeypatch.setattr(emb_module, "EmbeddingEngine", _FakeEngine)
    return TestClient(app)


def _mk_person(session, name: str, email: str | None = None) -> PersonORM:
    p = PersonORM(person_id=f"p-{uuid.uuid4().hex[:8]}", name=name, email=email)
    session.add(p)
    session.commit()
    return p


def _mk_meeting(
    session,
    *,
    title: str,
    meeting_type: str,
    date: datetime,
    attendees: list[PersonORM],
    sentiment: str | None = None,
    participant_sentiments: dict[str, str] | None = None,
) -> MeetingORM:
    m = MeetingORM(
        meeting_id=f"m-{uuid.uuid4().hex[:8]}",
        title=title,
        date=date,
        meeting_type=meeting_type,
        status="final",
        duration="30 minutes",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session.add(m)
    session.flush()
    for a in attendees:
        session.execute(
            meeting_attendees.insert().values(meeting_id=m.meeting_id, person_id=a.person_id)
        )

    # Always attach a minutes row so sentiment / structured lookup works.
    structured: dict = {}
    if sentiment:
        structured["sentiment"] = sentiment
    if participant_sentiments:
        structured["participants"] = [
            {"name": name, "sentiment": s}
            for name, s in participant_sentiments.items()
        ]
    minutes = MinutesORM(
        meeting_id=m.meeting_id,
        minutes_id=f"mt-{uuid.uuid4().hex[:8]}",
        summary=f"Summary of {title}",
        markdown_content=f"# {title}",
        generated_at=datetime.now(timezone.utc),
        llm_model="test",
        sentiment=sentiment,
        structured_json=json.dumps(structured) if structured else None,
    )
    session.add(minutes)
    session.commit()
    return m


def _mk_action(
    session,
    meeting: MeetingORM,
    *,
    description: str,
    owner: str,
    status: str = "open",
    due_date: str | None = None,
) -> ActionItemORM:
    ai = ActionItemORM(
        action_item_id=f"a-{uuid.uuid4().hex[:8]}",
        meeting_id=meeting.meeting_id,
        description=description,
        owner=owner,
        status=status,
        due_date=due_date,
    )
    session.add(ai)
    session.commit()
    return ai


def _mk_decision(session, meeting: MeetingORM, *, description: str, made_by: str) -> DecisionORM:
    d = DecisionORM(
        decision_id=f"d-{uuid.uuid4().hex[:8]}",
        meeting_id=meeting.meeting_id,
        description=description,
        made_by=made_by,
    )
    session.add(d)
    session.commit()
    return d


def _mk_chunk(
    session, meeting: MeetingORM, *, text: str, chunk_type: str = "discussion_point",
) -> EmbeddingChunkORM:
    c = EmbeddingChunkORM(
        meeting_id=meeting.meeting_id,
        chunk_type=chunk_type,
        text=text,
        meeting_date=meeting.date.strftime("%Y-%m-%d") if meeting.date else None,
        meeting_type=meeting.meeting_type,
        created_at=datetime.now(timezone.utc),
    )
    session.add(c)
    session.commit()
    return c


# ---------------------------------------------------------------------------
# Endpoint — rejects empty attendee list
# ---------------------------------------------------------------------------


def test_brief_requires_people_param(client):
    r = client.get("/api/brief")
    assert r.status_code == 400


def test_brief_404_for_unknown_person(client):
    r = client.get("/api/brief?people=p-nope")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Full-endpoint test with three prior meetings
# ---------------------------------------------------------------------------


def test_briefing_with_three_prior_meetings(client, session):
    jon = _mk_person(session, "Jon")
    now = datetime.now(timezone.utc)

    m1 = _mk_meeting(
        session,
        title="Weekly sync",
        meeting_type="one_on_one",
        date=now - timedelta(days=21),
        attendees=[jon],
        sentiment="positive",
        participant_sentiments={"Jon": "positive"},
    )
    m2 = _mk_meeting(
        session,
        title="Weekly sync",
        meeting_type="one_on_one",
        date=now - timedelta(days=14),
        attendees=[jon],
        sentiment="neutral",
        participant_sentiments={"Jon": "neutral"},
    )
    m3 = _mk_meeting(
        session,
        title="Weekly sync",
        meeting_type="one_on_one",
        date=now - timedelta(days=7),
        attendees=[jon],
        sentiment="constructive",
        participant_sentiments={"Jon": "constructive"},
    )

    _mk_action(
        session, m1,
        description="Send quarterly plan", owner="Jon",
        status="open", due_date=(now - timedelta(days=3)).strftime("%Y-%m-%d"),
    )
    _mk_action(session, m2, description="Book offsite", owner="Jon", status="open")
    _mk_action(session, m3, description="Old completed work", owner="Jon", status="done")

    _mk_decision(session, m2, description="Switch to biweekly", made_by="Jon")

    _mk_chunk(session, m1, text="Performance review prep.", chunk_type="parking_lot")
    _mk_chunk(session, m2, text="Performance review prep.", chunk_type="parking_lot")
    _mk_chunk(session, m3, text="Budget planning for next quarter.", chunk_type="discussion_point")

    r = client.get(f"/api/brief?people={jon.person_id}")
    assert r.status_code == 200
    body = r.json()

    # Section 1: who & when last
    assert body["who_and_when_last"]["total_prior_meetings"] == 3
    assert body["who_and_when_last"]["last_meeting_title"] == "Weekly sync"
    assert body["who_and_when_last"]["cadence"] == "weekly"

    # Section 2: open commitments, overdue flagged
    opens = body["open_commitments"]
    assert len(opens) == 2
    overdue = [c for c in opens if c["overdue"]]
    assert len(overdue) == 1
    assert overdue[0]["description"] == "Send quarterly plan"

    # Section 3: unresolved topics — dedup by normalized text.
    topics = body["unresolved_topics"]
    texts = [t["text"] for t in topics]
    assert any("Performance review prep" in t for t in texts)

    # Section 4: sentiment series for Jon.
    sentiment = body["recent_sentiment"]
    assert jon.person_id in sentiment
    assert len(sentiment[jon.person_id]["scores"]) >= 3

    # Section 5: decisions
    decisions = body["recent_decisions"]
    assert any(d["description"] == "Switch to biweekly" for d in decisions)

    # Section 7: suggested start
    start = body["suggested_start"]
    assert start["meeting_type"] == "one_on_one"
    assert "Jon" in start["attendee_labels"]
    # Carry-forward should mention the overdue item.
    assert "Send quarterly plan" in start["carry_forward_note"]

    # Summary omitted by default (summarize_with_llm=False)
    assert body.get("summary") is None


# ---------------------------------------------------------------------------
# Overdue detection
# ---------------------------------------------------------------------------


def test_overdue_detection_flags_past_due(session):
    jon = _mk_person(session, "Jon")
    now = datetime.now(timezone.utc)
    m = _mk_meeting(
        session,
        title="1:1",
        meeting_type="one_on_one",
        date=now - timedelta(days=2),
        attendees=[jon],
    )
    _mk_action(
        session, m,
        description="Overdue task", owner="Jon",
        due_date=(now - timedelta(days=1)).strftime("%Y-%m-%d"),
    )
    _mk_action(
        session, m,
        description="Not yet due", owner="Jon",
        due_date=(now + timedelta(days=10)).strftime("%Y-%m-%d"),
    )
    _mk_action(session, m, description="No date", owner="Jon")

    meetings = _meetings_with_any_attendee(session, [jon.person_id])
    opens = _build_open_commitments(session, [jon], meetings)

    assert len(opens) == 3
    overdue = [c for c in opens if c.overdue]
    assert len(overdue) == 1
    assert overdue[0].description == "Overdue task"


# ---------------------------------------------------------------------------
# Series detection — briefing surfaces the matching series
# ---------------------------------------------------------------------------


def test_series_is_surfaced_in_briefing(session):
    jon = _mk_person(session, "Jon")
    # Create a series whose attendee_hash matches Jon's sole attendee set.
    attendee_hash = compute_attendee_hash([jon.person_id])
    series = MeetingSeriesORM(
        series_id="s-jon",
        title="1:1 with Jon (weekly)",
        meeting_type="one_on_one",
        cadence="weekly",
        attendee_hash=attendee_hash,
        created_at=datetime.now(timezone.utc),
        last_detected_at=datetime.now(timezone.utc),
    )
    session.add(series)
    session.commit()

    # Three member meetings.
    now = datetime.now(timezone.utc)
    for i in range(3):
        m = _mk_meeting(
            session,
            title="1:1",
            meeting_type="one_on_one",
            date=now - timedelta(days=7 * (3 - i)),
            attendees=[jon],
        )
        session.add(MeetingSeriesMemberORM(series_id=series.series_id, meeting_id=m.meeting_id))
    session.commit()

    meetings = _meetings_with_any_attendee(session, [jon.person_id])
    who = _build_who_and_when_last(session, [jon], meetings)

    assert who.series is not None
    assert who.series.series_id == "s-jon"
    assert who.series.member_count == 3


# ---------------------------------------------------------------------------
# No-prior-meetings case
# ---------------------------------------------------------------------------


def test_no_prior_meetings_returns_empty_sections(client, session):
    jon = _mk_person(session, "Jon")
    r = client.get(f"/api/brief?people={jon.person_id}")
    assert r.status_code == 200
    body = r.json()

    assert body["who_and_when_last"]["total_prior_meetings"] == 0
    assert body["who_and_when_last"]["last_meeting_id"] is None
    assert body["open_commitments"] == []
    assert body["unresolved_topics"] == []
    assert body["recent_decisions"] == []
    assert body["context_excerpts"] == []
    # suggested_start still renders so the user can still record.
    assert body["suggested_start"]["attendee_labels"] == ["Jon"]
    # carry-forward falls back to placeholder.
    assert "no open commitments" in body["suggested_start"]["carry_forward_note"]


# ---------------------------------------------------------------------------
# Optional LLM summary toggle
# ---------------------------------------------------------------------------


def _stub_embedding_engine(monkeypatch):
    """Avoid loading the sentence-transformer model in unit tests."""
    import meeting_minutes.embeddings as emb_module

    class _FakeEngine:
        def __init__(self, *_a, **_kw): ...

        def search(self, *_a, **_kw):
            return []

    monkeypatch.setattr(emb_module, "EmbeddingEngine", _FakeEngine)


def test_summarize_with_llm_attaches_summary(session, monkeypatch):
    _stub_embedding_engine(monkeypatch)
    jon = _mk_person(session, "Jon")
    _mk_meeting(
        session,
        title="1:1",
        meeting_type="one_on_one",
        date=datetime.now(timezone.utc) - timedelta(days=3),
        attendees=[jon],
    )

    cfg = AppConfig()
    cfg.brief.summarize_with_llm = True

    # Patch the LLM summarizer to skip the network call.
    async def fake_summary(cfg, payload):
        return "Be ready to discuss roadmap and blockers. Confirm next-steps."

    monkeypatch.setattr(
        "meeting_minutes.api.routes.brief._maybe_summarize", fake_summary,
    )

    payload = asyncio.run(get_briefing(session=session, config=cfg, people=[jon.person_id], type=None))  # type: ignore[arg-type]
    assert payload.summary is not None
    assert payload.summary.startswith("Be ready")


def test_summarize_with_llm_disabled_omits_summary(session, monkeypatch):
    _stub_embedding_engine(monkeypatch)
    jon = _mk_person(session, "Jon")
    _mk_meeting(
        session,
        title="1:1",
        meeting_type="one_on_one",
        date=datetime.now(timezone.utc) - timedelta(days=3),
        attendees=[jon],
    )
    cfg = AppConfig()
    assert cfg.brief.summarize_with_llm is False

    payload = asyncio.run(get_briefing(session=session, config=cfg, people=[jon.person_id], type=None))  # type: ignore[arg-type]
    assert payload.summary is None
