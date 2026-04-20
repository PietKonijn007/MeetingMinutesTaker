"""Tests for DOCX export + series ZIP bundling (EXP-1)."""

from __future__ import annotations

import io
import uuid
import zipfile
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from meeting_minutes.api.deps import get_db_session, get_storage
from meeting_minutes.api.routes.meetings import router as meetings_router
from meeting_minutes.api.routes.series import router as series_router
from meeting_minutes.system3.db import (
    ActionItemORM,
    MeetingORM,
    MeetingSeriesMemberORM,
    MeetingSeriesORM,
    MinutesORM,
    TranscriptORM,
    create_tables,
)


pytest.importorskip("docx")


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


def _mk_meeting(
    session,
    *,
    title: str = "Team sync",
    with_transcript: bool = False,
    with_actions: bool = True,
) -> MeetingORM:
    now = datetime.now(timezone.utc)
    m = MeetingORM(
        meeting_id=f"m-{uuid.uuid4().hex[:8]}",
        title=title,
        date=now,
        duration="30 minutes",
        meeting_type="team_meeting",
        status="final",
        created_at=now,
        updated_at=now,
    )
    session.add(m)
    session.flush()
    minutes = MinutesORM(
        meeting_id=m.meeting_id,
        minutes_id=f"mt-{uuid.uuid4().hex[:8]}",
        summary="Short sync.",
        markdown_content=(
            f"# {title}\n\n"
            "## Summary\nTeam aligned on priorities.\n\n"
            "## Decisions\n- Adopt new tooling.\n\n"
            "## Action Items\n"
            "- [ ] Investigate option A — Owner: Alice\n"
            "- [x] Book room — Owner: Bob\n"
        ),
        generated_at=now,
        llm_model="test-model",
    )
    session.add(minutes)
    if with_actions:
        session.add(
            ActionItemORM(
                action_item_id=f"a-{uuid.uuid4().hex[:8]}",
                meeting_id=m.meeting_id,
                description="Investigate option A",
                owner="Alice",
                status="open",
                priority="high",
                due_date="2026-05-01",
            )
        )
    if with_transcript:
        session.add(
            TranscriptORM(
                meeting_id=m.meeting_id,
                full_text="Alice: Hi. Bob: Hi. UNIQUEDOCXTRANSCRIPT",
                language="en",
            )
        )
    session.commit()
    return m


def test_render_docx_produces_valid_zip_bytes(session):
    from meeting_minutes.export.docx import render_docx

    m = _mk_meeting(session)
    data = render_docx(m)
    # docx is a ZIP container — first two bytes are 'PK'.
    assert data[:2] == b"PK"


def test_docx_contains_action_items_table(session):
    from meeting_minutes.export.docx import render_docx

    m = _mk_meeting(session)
    data = render_docx(m)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        document_xml = zf.read("word/document.xml").decode("utf-8")
    # Table headers appear somewhere in the XML when actions were rendered.
    assert "Investigate option A" in document_xml
    assert "Alice" in document_xml
    assert "Action Items" in document_xml


def test_docx_with_transcript_appends_section(session):
    from meeting_minutes.export.docx import render_docx

    m = _mk_meeting(session, with_transcript=True)
    data = render_docx(m, with_transcript=True)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        document_xml = zf.read("word/document.xml").decode("utf-8")
    assert "Full Transcript" in document_xml
    assert "UNIQUEDOCXTRANSCRIPT" in document_xml


def test_docx_missing_dep_returns_501_via_api(session_factory, monkeypatch):
    from meeting_minutes.export import ExportDependencyMissing
    import meeting_minutes.export.docx as docx_mod
    from meeting_minutes.system3.storage import StorageEngine

    s = session_factory()
    try:
        m = _mk_meeting(s)
        meeting_id = m.meeting_id
    finally:
        s.close()

    def fake_require(*_a, **_kw):
        raise ExportDependencyMissing(
            "Install python-docx to enable DOCX export: pip install python-docx"
        )

    monkeypatch.setattr(docx_mod, "_require_docx", fake_require)

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

    client = TestClient(app)
    r = client.get(f"/api/meetings/{meeting_id}/export?format=docx")
    assert r.status_code == 501
    assert "python-docx" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Series ZIP export
# ---------------------------------------------------------------------------


def test_series_zip_contains_one_entry_per_member(session_factory):
    s = session_factory()
    try:
        # Build a series with three meetings.
        now = datetime.now(timezone.utc)
        series = MeetingSeriesORM(
            series_id="s-test",
            title="Weekly team sync",
            meeting_type="team_meeting",
            cadence="weekly",
            attendee_hash="deadbeef",
            created_at=now,
            last_detected_at=now,
        )
        s.add(series)
        ids = []
        for i in range(3):
            m = _mk_meeting(s, title=f"Team sync #{i+1}")
            s.add(
                MeetingSeriesMemberORM(
                    series_id=series.series_id, meeting_id=m.meeting_id
                )
            )
            ids.append(m.meeting_id)
        s.commit()
    finally:
        s.close()

    app = FastAPI()
    app.include_router(series_router)

    def override_session():
        s2 = session_factory()
        try:
            yield s2
        finally:
            s2.close()

    app.dependency_overrides[get_db_session] = override_session

    client = TestClient(app)
    r = client.get("/api/series/s-test/export?format=docx")
    assert r.status_code == 200, r.text
    assert r.content[:2] == b"PK"
    assert "attachment" in r.headers.get("content-disposition", "")

    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        names = zf.namelist()
    assert len(names) == 3
    assert all(n.endswith(".docx") for n in names)
