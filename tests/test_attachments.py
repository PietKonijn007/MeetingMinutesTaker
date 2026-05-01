"""Tests for the attachments foundation (spec/09-attachments.md).

Covers: storage CRUD, sidecar markdown round-trip, PDF text-layer
extraction, async worker, and the API surface (upload/list/get/raw/delete).

The summarizer + pipeline injection batches ship later; these tests stay
narrow on the foundation.
"""

from __future__ import annotations

import asyncio
import io
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from meeting_minutes.api.deps import get_config, get_db_session
from meeting_minutes.api.routes.attachments import router
from meeting_minutes.attachments import sidecar as sidecar_mod
from meeting_minutes.attachments import storage as storage_mod
from meeting_minutes.attachments import worker as worker_mod
from meeting_minutes.attachments.extractors import (
    ExtractionError,
    extract,
    extract_pdf,
)
from meeting_minutes.attachments.storage import DuplicateAttachment
from meeting_minutes.config import AppConfig
from meeting_minutes.system3.db import (
    AttachmentORM,
    MeetingORM,
    create_tables,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def session_factory():
    """In-memory SQLite that survives across threads (TestClient uses one)."""
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
def data_dir(tmp_path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def meeting_id(session) -> str:
    """Insert a stub meeting so the FK is satisfiable."""
    mid = f"m-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)
    session.add(
        MeetingORM(
            meeting_id=mid,
            title="Test meeting",
            date=now,
            meeting_type="standup",
            status="final",
            duration="15 minutes",
            created_at=now,
            updated_at=now,
        )
    )
    session.commit()
    return mid


def _minimal_pdf_bytes(text: str = "Hello attachment world") -> bytes:
    """Build a tiny single-page PDF using pypdf so tests don't ship a binary fixture.

    Embeds ``text`` on a single page; text-layer extraction returns it verbatim
    (modulo whitespace).
    """
    from pypdf import PdfWriter
    from pypdf.generic import (
        ArrayObject,
        DecodedStreamObject,
        DictionaryObject,
        FloatObject,
        NameObject,
        NumberObject,
    )

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    # Build a simple content stream that uses the standard /Helvetica font.
    # Coordinates put the text near the top of the page.
    safe = text.replace("(", r"\(").replace(")", r"\)")
    content = (
        f"BT /F1 24 Tf 72 720 Td ({safe}) Tj ET".encode("latin-1")
    )
    stream = DecodedStreamObject()
    stream.set_data(content)
    page[NameObject("/Contents")] = stream

    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    resources = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    page[NameObject("/Resources")] = resources
    page[NameObject("/MediaBox")] = ArrayObject(
        [NumberObject(0), NumberObject(0), FloatObject(612), FloatObject(792)]
    )

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Sidecar
# ---------------------------------------------------------------------------


def test_sidecar_round_trip(tmp_path):
    """Write then parse — every field survives, including empty summary."""
    path = tmp_path / "abc.md"
    sidecar_mod.write_attachment_sidecar(
        path,
        frontmatter={
            "attachment_id": "abc",
            "meeting_id": "m1",
            "kind": "file",
            "title": "Q3 forecast",
            "summary_status": "pending",
        },
        extracted="--- Page 1 ---\nLine one\nLine two",
        summary="",
    )
    parsed = sidecar_mod.parse_attachment_sidecar(path)
    assert parsed.frontmatter["attachment_id"] == "abc"
    assert parsed.frontmatter["title"] == "Q3 forecast"
    assert parsed.frontmatter["summary_status"] == "pending"
    assert "Line one" in parsed.extracted
    assert parsed.summary == ""


def test_sidecar_update_summary(tmp_path):
    """update_summary preserves extracted text and sets the status fields."""
    path = tmp_path / "abc.md"
    sidecar_mod.write_attachment_sidecar(
        path,
        frontmatter={
            "attachment_id": "abc",
            "meeting_id": "m1",
            "summary_status": "pending",
        },
        extracted="EXTRACTED BODY",
        summary="",
    )

    sidecar_mod.update_summary(
        path,
        summary="Two-sentence summary.\nSecond line.",
        summary_status="ready",
        summary_target="short",
    )

    parsed = sidecar_mod.parse_attachment_sidecar(path)
    assert parsed.summary.startswith("Two-sentence summary.")
    assert parsed.extracted == "EXTRACTED BODY"
    assert parsed.frontmatter["summary_status"] == "ready"
    assert parsed.frontmatter["summary_target"] == "short"


def test_sidecar_missing_file_returns_empty(tmp_path):
    parsed = sidecar_mod.parse_attachment_sidecar(tmp_path / "does_not_exist.md")
    assert parsed.frontmatter == {}
    assert parsed.summary == ""
    assert parsed.extracted == ""


# ---------------------------------------------------------------------------
# PDF extractor
# ---------------------------------------------------------------------------


def test_extract_pdf_text_layer(tmp_path):
    pdf_bytes = _minimal_pdf_bytes("Hello attachment world")
    path = tmp_path / "tiny.pdf"
    path.write_bytes(pdf_bytes)

    text, method = extract_pdf(path)
    assert method == "pdf-text-layer"
    assert "Hello attachment world" in text
    assert "--- Page 1 ---" in text


def test_extract_dispatches_by_mime(tmp_path):
    path = tmp_path / "tiny.pdf"
    path.write_bytes(_minimal_pdf_bytes())
    text, method = extract(path, "application/pdf")
    assert method == "pdf-text-layer"
    assert text  # non-empty


def test_extract_unknown_mime_raises(tmp_path):
    path = tmp_path / "weird.xyz"
    path.write_bytes(b"whatever")
    with pytest.raises(ExtractionError):
        extract(path, "application/x-unknown")


# ---------------------------------------------------------------------------
# Storage CRUD
# ---------------------------------------------------------------------------


def test_add_file_persists_row_and_disk(session, data_dir, meeting_id):
    raw = b"some bytes"
    row = storage_mod.add_file(
        session=session,
        data_dir=data_dir,
        meeting_id=meeting_id,
        fileobj=io.BytesIO(raw),
        original_filename="hello.pdf",
        mime_type="application/pdf",
        title="Hello",
    )
    session.commit()

    assert row.attachment_id
    assert row.kind == "file"
    assert row.size_bytes == len(raw)
    assert row.status == "pending"
    assert row.title == "Hello"

    on_disk = storage_mod.original_path(
        data_dir, meeting_id, row.attachment_id, ".pdf"
    )
    assert on_disk.exists()
    assert on_disk.read_bytes() == raw


def test_add_file_dedupe_returns_existing(session, data_dir, meeting_id):
    raw = b"identical content"
    first = storage_mod.add_file(
        session=session,
        data_dir=data_dir,
        meeting_id=meeting_id,
        fileobj=io.BytesIO(raw),
        original_filename="a.pdf",
        mime_type="application/pdf",
    )
    session.commit()

    with pytest.raises(DuplicateAttachment) as exc_info:
        storage_mod.add_file(
            session=session,
            data_dir=data_dir,
            meeting_id=meeting_id,
            fileobj=io.BytesIO(raw),
            original_filename="b.pdf",
            mime_type="application/pdf",
        )
    assert exc_info.value.attachment_id == first.attachment_id


def test_add_file_unknown_meeting_raises(session, data_dir):
    with pytest.raises(ValueError):
        storage_mod.add_file(
            session=session,
            data_dir=data_dir,
            meeting_id="does-not-exist",
            fileobj=io.BytesIO(b"x"),
            original_filename="x.pdf",
            mime_type="application/pdf",
        )


def test_list_for_meeting_orders_by_created(session, data_dir, meeting_id):
    for i in range(3):
        storage_mod.add_file(
            session=session,
            data_dir=data_dir,
            meeting_id=meeting_id,
            fileobj=io.BytesIO(f"content {i}".encode()),
            original_filename=f"file{i}.pdf",
            mime_type="application/pdf",
        )
    session.commit()
    rows = storage_mod.list_for_meeting(session, meeting_id)
    assert len(rows) == 3
    assert rows[0].original_filename == "file0.pdf"
    assert rows[2].original_filename == "file2.pdf"


def test_delete_drops_row_and_files(session, data_dir, meeting_id, tmp_path):
    row = storage_mod.add_file(
        session=session,
        data_dir=data_dir,
        meeting_id=meeting_id,
        fileobj=io.BytesIO(b"goodbye"),
        original_filename="bye.pdf",
        mime_type="application/pdf",
    )
    session.commit()
    aid = row.attachment_id
    on_disk = storage_mod.original_path(data_dir, meeting_id, aid, ".pdf")
    sidecar_path = storage_mod.sidecar_path(data_dir, meeting_id, aid)
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    sidecar_path.write_text("placeholder", encoding="utf-8")

    assert storage_mod.delete(session, data_dir, aid) is True
    session.commit()
    assert session.get(AttachmentORM, aid) is None
    assert not on_disk.exists()
    assert not sidecar_path.exists()


def test_delete_unknown_returns_false(session, data_dir):
    assert storage_mod.delete(session, data_dir, "no-such-id") is False


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


def test_worker_extracts_pdf_and_writes_sidecar(
    session_factory, data_dir, meeting_id
):
    """End-to-end: row + file on disk → worker → sidecar populated, status=ready."""
    session = session_factory()
    try:
        row = storage_mod.add_file(
            session=session,
            data_dir=data_dir,
            meeting_id=meeting_id,
            fileobj=io.BytesIO(_minimal_pdf_bytes("Pipeline-test text")),
            original_filename="doc.pdf",
            mime_type="application/pdf",
        )
        session.commit()
        attachment_id = row.attachment_id
    finally:
        session.close()

    cfg = AppConfig(data_dir=str(data_dir))

    asyncio.run(
        worker_mod.process_attachment(
            cfg, attachment_id, session_factory=session_factory
        )
    )

    # Re-read from a fresh session to see committed state.
    session = session_factory()
    try:
        refreshed = session.get(AttachmentORM, attachment_id)
        assert refreshed is not None
        assert refreshed.status == "ready"

        sidecar = sidecar_mod.parse_attachment_sidecar(
            storage_mod.sidecar_path(data_dir, meeting_id, attachment_id)
        )
        assert sidecar.frontmatter["extraction_method"] == "pdf-text-layer"
        assert sidecar.frontmatter["summary_status"] == "pending"
        assert "Pipeline-test text" in sidecar.extracted
    finally:
        session.close()


def test_worker_records_error_when_file_missing(
    session_factory, data_dir, meeting_id
):
    """If the original file is gone, worker flips status=error cleanly."""
    session = session_factory()
    try:
        row = storage_mod.add_file(
            session=session,
            data_dir=data_dir,
            meeting_id=meeting_id,
            fileobj=io.BytesIO(b"placeholder"),
            original_filename="x.pdf",
            mime_type="application/pdf",
        )
        session.commit()
        attachment_id = row.attachment_id
        # Delete the on-disk original out from under the worker.
        storage_mod.original_path(data_dir, meeting_id, attachment_id, ".pdf").unlink()
    finally:
        session.close()

    cfg = AppConfig(data_dir=str(data_dir))
    asyncio.run(
        worker_mod.process_attachment(
            cfg, attachment_id, session_factory=session_factory
        )
    )

    session = session_factory()
    try:
        refreshed = session.get(AttachmentORM, attachment_id)
        assert refreshed.status == "error"
        assert refreshed.error and "missing" in refreshed.error.lower()
    finally:
        session.close()


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@pytest.fixture
def app(session_factory, data_dir):
    """A FastAPI app with only the attachments router mounted."""
    app = FastAPI()
    app.include_router(router)

    def override_session():
        s = session_factory()
        try:
            yield s
        finally:
            s.close()

    def override_config():
        return AppConfig(data_dir=str(data_dir))

    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_config] = override_config
    return app


@pytest.fixture
def client(app, monkeypatch):
    """TestClient with the worker stubbed out — handler tests don't need to
    actually run extraction; that's covered separately."""

    def _no_op_schedule(_config, _attachment_id):
        async def _noop():
            return None

        return asyncio.get_event_loop().create_task(_noop())

    monkeypatch.setattr(worker_mod, "schedule", _no_op_schedule)
    return TestClient(app)


def test_api_upload_creates_row(client, meeting_id, data_dir):
    pdf = _minimal_pdf_bytes("API upload body")
    response = client.post(
        f"/api/meetings/{meeting_id}/attachments",
        files={"file": ("hello.pdf", pdf, "application/pdf")},
        data={"title": "Hello", "caption": "from a test"},
    )
    assert response.status_code == 202, response.text
    payload = response.json()
    assert payload["title"] == "Hello"
    assert payload["caption"] == "from a test"
    assert payload["mime_type"] == "application/pdf"
    assert payload["status"] == "pending"

    # File ended up on disk under the meeting's folder.
    target_dir = data_dir / "attachments" / meeting_id
    assert target_dir.exists()
    files = list(target_dir.iterdir())
    assert any(f.suffix == ".pdf" for f in files)


def test_api_upload_unknown_meeting_404(client):
    response = client.post(
        "/api/meetings/no-such-meeting/attachments",
        files={"file": ("x.pdf", b"data", "application/pdf")},
    )
    assert response.status_code == 404


def test_api_upload_rejects_disallowed_mime(client, meeting_id):
    response = client.post(
        f"/api/meetings/{meeting_id}/attachments",
        files={"file": ("evil.exe", b"MZ\x00\x00", "application/x-msdownload")},
    )
    assert response.status_code == 415


def test_api_upload_dedup_returns_existing(client, meeting_id):
    pdf = _minimal_pdf_bytes("dedup body")
    first = client.post(
        f"/api/meetings/{meeting_id}/attachments",
        files={"file": ("a.pdf", pdf, "application/pdf")},
    )
    assert first.status_code == 202
    aid = first.json()["attachment_id"]

    second = client.post(
        f"/api/meetings/{meeting_id}/attachments",
        files={"file": ("b.pdf", pdf, "application/pdf")},
    )
    # Dedup path returns the existing summary — handler returns 202 default
    # for the route, so we accept either 200 or 202 as long as the id matches.
    assert second.status_code in (200, 202)
    assert second.json()["attachment_id"] == aid


def test_api_list_returns_uploads(client, meeting_id):
    for i in range(2):
        client.post(
            f"/api/meetings/{meeting_id}/attachments",
            files={"file": (f"f{i}.pdf", _minimal_pdf_bytes(f"body {i}"), "application/pdf")},
        )

    response = client.get(f"/api/meetings/{meeting_id}/attachments")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 2
    filenames = sorted(item["original_filename"] for item in payload)
    assert filenames == ["f0.pdf", "f1.pdf"]


def test_api_get_detail_includes_sidecar(
    client, session_factory, data_dir, meeting_id
):
    """If a sidecar exists, the detail endpoint surfaces it."""
    upload = client.post(
        f"/api/meetings/{meeting_id}/attachments",
        files={"file": ("doc.pdf", _minimal_pdf_bytes("body"), "application/pdf")},
    )
    aid = upload.json()["attachment_id"]

    # Stub sidecar so we don't need the worker to actually run.
    sidecar_mod.write_attachment_sidecar(
        storage_mod.sidecar_path(data_dir, meeting_id, aid),
        frontmatter={
            "attachment_id": aid,
            "meeting_id": meeting_id,
            "summary_status": "ready",
        },
        extracted="EXTRACTED",
        summary="Short summary.",
    )

    response = client.get(f"/api/attachments/{aid}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"] == "Short summary."
    assert payload["extracted_text"] == "EXTRACTED"
    assert payload["summary_status"] == "ready"


def test_api_raw_returns_original_bytes(client, meeting_id):
    pdf = _minimal_pdf_bytes("raw body")
    upload = client.post(
        f"/api/meetings/{meeting_id}/attachments",
        files={"file": ("doc.pdf", pdf, "application/pdf")},
    )
    aid = upload.json()["attachment_id"]

    response = client.get(f"/api/attachments/{aid}/raw")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.content == pdf


def test_api_delete_removes_row(client, session_factory, data_dir, meeting_id):
    upload = client.post(
        f"/api/meetings/{meeting_id}/attachments",
        files={"file": ("doc.pdf", _minimal_pdf_bytes("body"), "application/pdf")},
    )
    aid = upload.json()["attachment_id"]

    response = client.delete(f"/api/attachments/{aid}")
    assert response.status_code == 204

    # Subsequent GET 404s.
    follow = client.get(f"/api/attachments/{aid}")
    assert follow.status_code == 404
