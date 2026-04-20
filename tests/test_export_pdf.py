"""Tests for PDF export (EXP-1)."""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from meeting_minutes.api.deps import get_db_session, get_storage
from meeting_minutes.api.routes.meetings import router as meetings_router
from meeting_minutes.system3.db import (
    MeetingORM,
    MinutesORM,
    TranscriptORM,
    create_tables,
)


# Skip all tests in this module if weasyprint isn't importable (native libs
# not installed on this host). This keeps CI green on Linux/Windows runners
# where the native deps are missing while still exercising the code path
# locally on the maintainer's machine.
weasyprint = pytest.importorskip("weasyprint")


@pytest.fixture
def session_factory():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    create_tables(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.fixture
def session(session_factory):
    s = session_factory()
    yield s
    s.close()


def _mk_meeting(session, *, with_transcript: bool = False) -> MeetingORM:
    now = datetime.now(timezone.utc)
    m = MeetingORM(
        meeting_id=f"m-{uuid.uuid4().hex[:8]}",
        title="Quarterly roadmap review",
        date=now,
        duration="60 minutes",
        meeting_type="planning",
        status="final",
        created_at=now,
        updated_at=now,
    )
    session.add(m)
    session.flush()
    minutes = MinutesORM(
        meeting_id=m.meeting_id,
        minutes_id=f"mt-{uuid.uuid4().hex[:8]}",
        summary="Planning discussion.",
        markdown_content=(
            "# Quarterly roadmap review\n\n"
            "## Summary\n"
            "Team reviewed Q2 priorities.\n\n"
            "## Decisions\n"
            "- Ship feature X in May.\n\n"
            "## Action Items\n"
            "- [ ] Draft spec — Owner: Alice (Due: 2026-05-01)\n"
            "- [x] Book design review — Owner: Bob\n"
        ),
        generated_at=now,
        llm_model="test-model",
    )
    session.add(minutes)
    if with_transcript:
        t = TranscriptORM(
            meeting_id=m.meeting_id,
            full_text="Alice: Let's start. Bob: OK. UNIQUETRANSCRIPTTOKEN",
            language="en",
        )
        session.add(t)
    session.commit()
    return m


def test_render_pdf_produces_valid_pdf_bytes(session):
    from meeting_minutes.export.pdf import render_pdf

    m = _mk_meeting(session)
    pdf = render_pdf(m)
    assert isinstance(pdf, (bytes, bytearray))
    assert pdf[:4] == b"%PDF"


def test_with_transcript_appends_transcript_section(session):
    from meeting_minutes.export.pdf import _build_html, render_pdf

    m = _mk_meeting(session, with_transcript=True)
    # Check the rendered HTML directly — PDF bytes are compressed so byte-grep
    # isn't a reliable signal. The builder is the layer we want to assert on.
    html = _build_html(m, with_transcript=True)
    assert "Full Transcript" in html
    assert "UNIQUETRANSCRIPTTOKEN" in html

    # The PDF itself still renders without error.
    pdf = render_pdf(m, with_transcript=True)
    assert pdf[:4] == b"%PDF"


def test_without_transcript_skips_section(session):
    from meeting_minutes.export.pdf import _build_html

    m = _mk_meeting(session, with_transcript=True)
    html = _build_html(m, with_transcript=False)
    assert "UNIQUETRANSCRIPTTOKEN" not in html
    assert "Full Transcript" not in html


def test_missing_minutes_raises(session):
    from meeting_minutes.export import export

    m = MeetingORM(
        meeting_id=f"m-{uuid.uuid4().hex[:8]}",
        title="No minutes yet",
        date=datetime.now(timezone.utc),
        status="draft",
    )
    session.add(m)
    session.commit()
    with pytest.raises(ValueError):
        export(m, format="pdf")


def _build_client(session_factory) -> tuple[TestClient, str]:
    """Boot a FastAPI app with meetings_router and a seeded meeting.

    Because the meeting references lazy-loaded relationships (transcript,
    attendees, minutes) the ORM instance must never outlive its session.
    Here we seed it inside one session, return only the ``meeting_id``,
    and let the route's own session handle refetching.
    """
    from meeting_minutes.system3.storage import StorageEngine

    s = session_factory()
    try:
        m = _mk_meeting(s)
        meeting_id = m.meeting_id
    finally:
        s.close()

    app = FastAPI()
    app.include_router(meetings_router)

    def override_session():
        s2 = session_factory()
        try:
            yield s2
        finally:
            s2.close()

    def override_storage():
        s3 = session_factory()
        try:
            yield StorageEngine(s3)
        finally:
            s3.close()

    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_storage] = override_storage
    return TestClient(app), meeting_id


def test_api_endpoint_returns_pdf_bytes(session_factory):
    """Full GET /api/meetings/{id}/export?format=pdf round-trip."""
    client, meeting_id = _build_client(session_factory)
    r = client.get(f"/api/meetings/{meeting_id}/export?format=pdf")
    assert r.status_code == 200, r.text
    assert r.content[:4] == b"%PDF"
    disp = r.headers.get("content-disposition", "")
    assert "attachment" in disp
    assert ".pdf" in disp


def test_api_missing_dep_returns_501(session_factory, monkeypatch):
    """Simulate missing weasyprint — endpoint returns 501 with install hint."""
    from meeting_minutes.export import ExportDependencyMissing
    import meeting_minutes.export.pdf as pdf_mod

    def fake_require(*_a, **_kw):
        raise ExportDependencyMissing(
            "Install weasyprint to enable PDF export: pip install weasyprint"
        )

    monkeypatch.setattr(pdf_mod, "_require_weasyprint", fake_require)

    client, meeting_id = _build_client(session_factory)
    r = client.get(f"/api/meetings/{meeting_id}/export?format=pdf")
    assert r.status_code == 501
    assert "weasyprint" in r.json()["detail"].lower()
