"""Attachments API endpoints (spec/09-attachments.md).

This batch ships the file-upload boundary plus list/get/delete/raw.
Link uploads, paste handling, thumbnail generation, summary regeneration,
and the LLM summarizer follow in subsequent batches.

The upload handler:

1. Validates against ``config.attachments`` (enabled, size, mime).
2. Persists the file + DB row via :mod:`attachments.storage`.
3. Schedules the async extraction worker via ``asyncio.create_task``.
4. Returns 202 with the new attachment_id so the UI can start polling.

Re-uploads of the same content (matched by sha256 within the meeting)
return 200 with the existing attachment_id rather than failing — the UX
of dragging a file twice should be a no-op.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from meeting_minutes.api.deps import get_config, get_db_session
from meeting_minutes.attachments import sidecar as sidecar_mod
from meeting_minutes.attachments import storage as storage_mod
from meeting_minutes.attachments import worker as worker_mod
from meeting_minutes.attachments.storage import DuplicateAttachment
from meeting_minutes.config import AppConfig
from meeting_minutes.system3.db import AttachmentORM, MeetingORM

router = APIRouter(tags=["attachments"])


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class AttachmentSummary(BaseModel):
    """Compact representation used by list and create responses."""

    attachment_id: str
    meeting_id: str
    kind: str
    source: str
    title: str
    caption: str | None = None
    original_filename: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None
    status: str
    error: str | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_orm_row(cls, row: AttachmentORM) -> "AttachmentSummary":
        return cls(
            attachment_id=row.attachment_id,
            meeting_id=row.meeting_id,
            kind=row.kind,
            source=row.source,
            title=row.title,
            caption=row.caption,
            original_filename=row.original_filename,
            mime_type=row.mime_type,
            size_bytes=row.size_bytes,
            status=row.status,
            error=row.error,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class AttachmentDetail(AttachmentSummary):
    """Detail view — adds the parsed sidecar contents (extracted + summary)."""

    summary: str = ""
    extracted_text: str = ""
    summary_status: str | None = None
    url: str | None = None  # populated for kind='link'


class LinkAttachmentRequest(BaseModel):
    """Body for ``POST /api/meetings/{id}/attachments/link``.

    Only ``url`` is required; the worker will fall back to the page
    ``<title>`` when ``title`` isn't supplied.
    """

    url: str = Field(..., min_length=1)
    title: str | None = None
    caption: str | None = None


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


@router.post(
    "/api/meetings/{meeting_id}/attachments",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AttachmentSummary,
)
async def upload_attachment(
    meeting_id: str,
    config: Annotated[AppConfig, Depends(get_config)],
    session: Annotated[Session, Depends(get_db_session)],
    file: UploadFile = File(...),
    title: str = Form(""),
    caption: str = Form(""),
):
    """Accept a multipart file, persist it, kick off the async worker."""
    cfg = config.attachments
    if not cfg.enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Attachments are disabled in config.",
        )

    if session.get(MeetingORM, meeting_id) is None:
        raise HTTPException(status_code=404, detail=f"No meeting with ID {meeting_id}")

    # Mime allowlist. We trust the multipart Content-Type for now; sniffing
    # the first bytes of the upload is a follow-up.
    if file.content_type and file.content_type not in cfg.allowed_mime_types:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                f"MIME type {file.content_type!r} not in allowlist. "
                f"Allowed: {', '.join(cfg.allowed_mime_types)}"
            ),
        )

    data_dir = Path(config.data_dir).expanduser()

    # FastAPI exposes the uploaded body as a SpooledTemporaryFile via
    # ``file.file``. ``add_file`` streams from it so we don't have to
    # buffer the whole upload in memory.
    try:
        row = storage_mod.add_file(
            session=session,
            data_dir=data_dir,
            meeting_id=meeting_id,
            fileobj=file.file,
            original_filename=file.filename or "attachment",
            mime_type=file.content_type,
            title=title.strip() or None,
            caption=caption.strip() or None,
            source="upload",
        )
    except DuplicateAttachment as exc:
        # Same content already attached to this meeting. Return the
        # existing row so the client treats this as a no-op success.
        existing = storage_mod.get(session, exc.attachment_id)
        if existing is None:  # pragma: no cover - defensive
            raise HTTPException(status_code=500, detail="Duplicate row vanished")
        return AttachmentSummary.from_orm_row(existing)
    except ValueError as exc:
        # ``add_file`` raises ValueError when the meeting doesn't exist; we
        # already 404'd above but keep the guard in case of races.
        raise HTTPException(status_code=404, detail=str(exc))

    # Enforce the size cap *after* we know the on-disk size. We could
    # check ``file.size`` ahead of time, but it's not always set by
    # clients; comparing the actual bytes-on-disk is authoritative.
    max_bytes = cfg.max_file_size_mb * 1024 * 1024
    if (row.size_bytes or 0) > max_bytes:
        # Roll back — drop the row + file we just wrote.
        storage_mod.delete(session, data_dir, row.attachment_id)
        session.commit()
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"File too large: {row.size_bytes} bytes > "
                f"{cfg.max_file_size_mb} MB cap"
            ),
        )

    session.commit()

    # Fire-and-forget extraction. The worker writes its own session, so we
    # don't pass ours through — the request's session goes out of scope as
    # soon as this handler returns.
    worker_mod.schedule(config, row.attachment_id)

    return AttachmentSummary.from_orm_row(row)


@router.post(
    "/api/meetings/{meeting_id}/attachments/link",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AttachmentSummary,
)
async def add_link_attachment(
    meeting_id: str,
    body: LinkAttachmentRequest,
    config: Annotated[AppConfig, Depends(get_config)],
    session: Annotated[Session, Depends(get_db_session)],
):
    """Attach a URL to a meeting; worker fetches + summarizes asynchronously."""
    cfg = config.attachments
    if not cfg.enabled:
        raise HTTPException(status_code=403, detail="Attachments are disabled in config.")

    if session.get(MeetingORM, meeting_id) is None:
        raise HTTPException(status_code=404, detail=f"No meeting with ID {meeting_id}")

    # Cheap URL sanity check at the boundary — full validation lives in
    # the extractor, but rejecting obviously-bad inputs here keeps junk
    # rows out of the DB.
    url = body.url.strip()
    if not url.lower().startswith(("http://", "https://")):
        raise HTTPException(
            status_code=422,
            detail="URL must start with http:// or https://",
        )

    try:
        row = storage_mod.add_link(
            session=session,
            meeting_id=meeting_id,
            url=url,
            title=(body.title or "").strip() or None,
            caption=(body.caption or "").strip() or None,
        )
    except DuplicateAttachment as exc:
        existing = storage_mod.get(session, exc.attachment_id)
        if existing is None:  # pragma: no cover
            raise HTTPException(status_code=500, detail="Duplicate row vanished")
        return AttachmentSummary.from_orm_row(existing)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    session.commit()
    worker_mod.schedule(config, row.attachment_id)
    return AttachmentSummary.from_orm_row(row)


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


@router.get(
    "/api/meetings/{meeting_id}/attachments",
    response_model=list[AttachmentSummary],
)
async def list_attachments(
    meeting_id: str,
    session: Annotated[Session, Depends(get_db_session)],
):
    if session.get(MeetingORM, meeting_id) is None:
        raise HTTPException(status_code=404, detail=f"No meeting with ID {meeting_id}")
    rows = storage_mod.list_for_meeting(session, meeting_id)
    return [AttachmentSummary.from_orm_row(r) for r in rows]


@router.get(
    "/api/attachments/{attachment_id}",
    response_model=AttachmentDetail,
)
async def get_attachment(
    attachment_id: str,
    config: Annotated[AppConfig, Depends(get_config)],
    session: Annotated[Session, Depends(get_db_session)],
):
    row = storage_mod.get(session, attachment_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"No attachment with ID {attachment_id}")

    data_dir = Path(config.data_dir).expanduser()
    sidecar = sidecar_mod.parse_attachment_sidecar(
        storage_mod.sidecar_path(data_dir, row.meeting_id, attachment_id)
    )
    base = AttachmentSummary.from_orm_row(row).model_dump()
    return AttachmentDetail(
        **base,
        summary=sidecar.summary,
        extracted_text=sidecar.extracted,
        summary_status=sidecar.frontmatter.get("summary_status"),
        url=row.url,
    )


@router.get("/api/attachments/{attachment_id}/raw")
async def get_attachment_raw(
    attachment_id: str,
    config: Annotated[AppConfig, Depends(get_config)],
    session: Annotated[Session, Depends(get_db_session)],
):
    """Stream the original bytes back to the client.

    Sets a sensible Content-Disposition so the browser uses the original
    filename when the user clicks "Download" — important for users
    cross-checking the minutes against the source material.
    """
    row = storage_mod.get(session, attachment_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"No attachment with ID {attachment_id}")

    data_dir = Path(config.data_dir).expanduser()
    ext = Path(row.original_filename or "").suffix or ""
    path = storage_mod.original_path(data_dir, row.meeting_id, attachment_id, ext)
    if not path.exists():
        raise HTTPException(status_code=410, detail="Original file is no longer on disk")

    return FileResponse(
        str(path),
        media_type=row.mime_type or "application/octet-stream",
        filename=row.original_filename or f"{attachment_id}{ext}",
    )


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


@router.delete(
    "/api/attachments/{attachment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_attachment(
    attachment_id: str,
    config: Annotated[AppConfig, Depends(get_config)],
    session: Annotated[Session, Depends(get_db_session)],
):
    data_dir = Path(config.data_dir).expanduser()
    if not storage_mod.delete(session, data_dir, attachment_id):
        raise HTTPException(status_code=404, detail=f"No attachment with ID {attachment_id}")
    session.commit()
    return None
