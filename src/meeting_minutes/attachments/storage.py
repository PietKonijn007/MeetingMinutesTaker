"""Filesystem + DB operations for attachments.

Filesystem layout (per spec/09-attachments.md):

.. code-block::

    data/attachments/{meeting_id}/
        {attachment_id}{ext}        -- original bytes
        {attachment_id}.md          -- sidecar (extracted text + summary)
        {attachment_id}.thumb.jpg   -- thumbnail (added in next batch)

DB table: ``attachments`` — lightweight metadata only.

Public callers go through :func:`add_file`, :func:`list_for_meeting`,
:func:`get`, :func:`delete`. Each of those owns the FS+DB pair so the two
never drift out of sync.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from meeting_minutes.system3.db import AttachmentORM, MeetingORM

_LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def attachment_dir(data_dir: Path, meeting_id: str) -> Path:
    """Return the per-meeting attachments directory (not created)."""
    return data_dir / "attachments" / meeting_id


def original_path(data_dir: Path, meeting_id: str, attachment_id: str, ext: str) -> Path:
    """Path to the stored original file. ``ext`` includes the leading dot."""
    return attachment_dir(data_dir, meeting_id) / f"{attachment_id}{ext}"


def sidecar_path(data_dir: Path, meeting_id: str, attachment_id: str) -> Path:
    return attachment_dir(data_dir, meeting_id) / f"{attachment_id}.md"


# ---------------------------------------------------------------------------
# Public API — used by the API handlers and the worker
# ---------------------------------------------------------------------------


class DuplicateAttachment(Exception):
    """Raised by :func:`add_file` when (meeting_id, sha256) already exists.

    Carries the existing attachment_id so the handler can return 200 with
    the existing row rather than failing the upload — the user dragging
    in the same PDF twice should be a no-op, not an error.
    """

    def __init__(self, attachment_id: str) -> None:
        super().__init__(f"Attachment with same content already exists: {attachment_id}")
        self.attachment_id = attachment_id


def add_file(
    *,
    session: Session,
    data_dir: Path,
    meeting_id: str,
    fileobj: BinaryIO,
    original_filename: str,
    mime_type: str | None,
    title: str | None = None,
    caption: str | None = None,
    source: str = "upload",
) -> AttachmentORM:
    """Persist an uploaded file to disk + DB.

    Streams ``fileobj`` to disk while computing sha256 in one pass, so we
    don't have to buffer the whole file in memory (a 50 MB PDF doesn't
    need to live in RAM). The DB row is inserted last; if the insert fails
    the file is deleted so we don't leak orphans.

    Raises :class:`DuplicateAttachment` if the (meeting, content) pair
    already exists in this meeting.
    """
    if session.get(MeetingORM, meeting_id) is None:
        raise ValueError(f"No meeting with ID {meeting_id}")

    attachment_id = str(uuid.uuid4())
    ext = Path(original_filename).suffix or ""
    target_dir = attachment_dir(data_dir, meeting_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = original_path(data_dir, meeting_id, attachment_id, ext)

    sha256 = _stream_to_disk(fileobj, target_path)
    size = target_path.stat().st_size

    # Dedupe inside the meeting: same content uploaded twice → return
    # the existing row, drop the just-written file.
    existing = (
        session.query(AttachmentORM)
        .filter_by(meeting_id=meeting_id, sha256=sha256)
        .one_or_none()
    )
    if existing is not None:
        try:
            target_path.unlink()
        except OSError:
            pass
        raise DuplicateAttachment(existing.attachment_id)

    now = datetime.now(timezone.utc)
    row = AttachmentORM(
        attachment_id=attachment_id,
        meeting_id=meeting_id,
        kind=_kind_from_mime(mime_type, ext),
        source=source,
        original_filename=original_filename,
        mime_type=mime_type,
        size_bytes=size,
        sha256=sha256,
        url=None,
        title=title or original_filename,
        caption=caption,
        status="pending",
        error=None,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    try:
        session.flush()
    except IntegrityError:
        # Race: another concurrent upload of the same content won the
        # sha256 unique-index. Roll back, clean up our file, surface the
        # existing row.
        session.rollback()
        try:
            target_path.unlink()
        except OSError:
            pass
        existing = (
            session.query(AttachmentORM)
            .filter_by(meeting_id=meeting_id, sha256=sha256)
            .one()
        )
        raise DuplicateAttachment(existing.attachment_id)

    return row


def add_link(
    *,
    session: Session,
    meeting_id: str,
    url: str,
    title: str | None = None,
    caption: str | None = None,
    source: str = "upload",
) -> AttachmentORM:
    """Persist a link attachment.

    No filesystem write at this point — the link's content gets fetched
    inside the worker and stored in the sidecar markdown. Dedupe is by
    (meeting_id, url): re-pasting the same link is a no-op (returns the
    existing row via :class:`DuplicateAttachment`).
    """
    if session.get(MeetingORM, meeting_id) is None:
        raise ValueError(f"No meeting with ID {meeting_id}")

    normalized_url = (url or "").strip()
    if not normalized_url:
        raise ValueError("URL cannot be empty")

    existing = (
        session.query(AttachmentORM)
        .filter_by(meeting_id=meeting_id, kind="link", url=normalized_url)
        .one_or_none()
    )
    if existing is not None:
        raise DuplicateAttachment(existing.attachment_id)

    attachment_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    row = AttachmentORM(
        attachment_id=attachment_id,
        meeting_id=meeting_id,
        kind="link",
        source=source,
        original_filename=None,
        mime_type=None,
        size_bytes=None,
        sha256=None,
        url=normalized_url,
        title=title or normalized_url,
        caption=caption,
        status="pending",
        error=None,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.flush()
    return row


def get(session: Session, attachment_id: str) -> AttachmentORM | None:
    return session.get(AttachmentORM, attachment_id)


def list_for_meeting(session: Session, meeting_id: str) -> list[AttachmentORM]:
    return (
        session.query(AttachmentORM)
        .filter_by(meeting_id=meeting_id)
        .order_by(AttachmentORM.created_at.asc())
        .all()
    )


def delete(session: Session, data_dir: Path, attachment_id: str) -> bool:
    """Drop the DB row and the on-disk artifacts. Returns False if no row.

    Removes the original file and the sidecar; leaves the meeting's
    attachments folder in place even if it ends up empty (cheap and the
    folder gets cleaned up when the meeting itself is deleted).
    """
    row = session.get(AttachmentORM, attachment_id)
    if row is None:
        return False

    meeting_id = row.meeting_id
    ext = Path(row.original_filename or "").suffix or ""
    target = original_path(data_dir, meeting_id, attachment_id, ext)
    sidecar = sidecar_path(data_dir, meeting_id, attachment_id)

    session.delete(row)
    session.flush()

    for p in (target, sidecar):
        try:
            if p.exists():
                p.unlink()
        except OSError as exc:
            _LOG.warning("Failed to delete %s: %s", p, exc)

    return True


def update_status(
    session: Session,
    attachment_id: str,
    status: str,
    *,
    error: str | None = None,
) -> None:
    """Worker writes status transitions through here.

    Always clears ``error`` on non-error transitions so the row doesn't
    keep a stale message after a successful retry.
    """
    row = session.get(AttachmentORM, attachment_id)
    if row is None:
        return
    row.status = status
    row.error = error if status == "error" else None
    row.updated_at = datetime.now(timezone.utc)
    session.flush()


def delete_meeting_directory(data_dir: Path, meeting_id: str) -> None:
    """Remove the entire ``data/attachments/{meeting_id}/`` tree.

    Called when a meeting is deleted. Best-effort; logs and swallows on
    failure so meeting deletion never blocks on a stuck file handle.
    """
    target = attachment_dir(data_dir, meeting_id)
    if not target.exists():
        return
    try:
        shutil.rmtree(target)
    except OSError as exc:
        _LOG.warning("Failed to remove %s: %s", target, exc)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _stream_to_disk(fileobj: BinaryIO, target: Path) -> str:
    """Copy ``fileobj`` into ``target`` while computing sha256 in one pass."""
    h = hashlib.sha256()
    with target.open("wb") as out:
        while True:
            chunk = fileobj.read(64 * 1024)
            if not chunk:
                break
            h.update(chunk)
            out.write(chunk)
    return h.hexdigest()


def _kind_from_mime(mime_type: str | None, ext: str) -> str:
    """Map mime/extension → ``kind`` enum value used in the DB."""
    if mime_type and mime_type.startswith("image/"):
        return "image"
    if ext.lower() in {".png", ".jpg", ".jpeg", ".heic"}:
        return "image"
    return "file"
