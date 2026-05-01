"""Async worker — extract → write sidecar.

This batch ships extraction only. The summarization step (separate LLM
prompt with tiered length) lands in the next batch; it will plug in
between ``extract`` and the final ``status='ready'`` transition by
calling :func:`sidecar.update_summary` after the LLM call returns.

Fired from the API handler on every successful upload via
``asyncio.create_task``. Must never raise — failures are recorded on the
DB row's ``status='error'`` + ``error`` column and on the sidecar
frontmatter, so the UI can surface them.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from meeting_minutes.attachments import sidecar as sidecar_mod
from meeting_minutes.attachments import storage as storage_mod
from meeting_minutes.attachments.extractors import ExtractionError, extract
from meeting_minutes.config import AppConfig, resolve_db_path
from meeting_minutes.system3.db import AttachmentORM, get_session_factory

_LOG = logging.getLogger(__name__)


async def process_attachment(
    config: AppConfig,
    attachment_id: str,
    *,
    session_factory: Callable[[], object] | None = None,
) -> None:
    """End-to-end: load row → extract → write sidecar → mark ready.

    ``session_factory`` is injectable so tests can pass a sessionmaker
    bound to an in-memory DB. Production callers leave it ``None``; we
    build one against ``config.storage.sqlite_path``.
    """
    if session_factory is None:
        db_path = resolve_db_path(config.storage.sqlite_path)
        session_factory = get_session_factory(f"sqlite:///{db_path}")

    # Run the (potentially blocking) extraction in a thread so it doesn't
    # stall the event loop. Disk I/O + pypdf are CPU-bound; we don't want
    # to wedge the recording websocket while a 50 MB PDF parses.
    await asyncio.to_thread(_run, config, attachment_id, session_factory)


def _run(config: AppConfig, attachment_id: str, session_factory: Callable) -> None:
    """Blocking implementation of :func:`process_attachment`.

    Split out from the async wrapper so the heavy work runs cleanly under
    ``asyncio.to_thread``. Catches everything so the task surface stays
    quiet — the only side effect of failure is a status-flip on the row.
    """
    data_dir = Path(config.data_dir).expanduser()
    session = session_factory()
    try:
        row = session.get(AttachmentORM, attachment_id)
        if row is None:
            _LOG.warning("Attachment %s vanished before worker ran", attachment_id)
            return

        meeting_id = row.meeting_id
        original = storage_mod.original_path(
            data_dir,
            meeting_id,
            attachment_id,
            Path(row.original_filename or "").suffix or "",
        )
        if not original.exists():
            _set_error(
                session,
                attachment_id,
                meeting_id,
                f"Original file missing on disk: {original}",
            )
            return

        storage_mod.update_status(session, attachment_id, "extracting")
        session.commit()

        try:
            extracted, method = extract(original, row.mime_type)
        except ExtractionError as exc:
            _set_error(session, attachment_id, meeting_id, str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            _LOG.exception("Unexpected extraction failure for %s", attachment_id)
            _set_error(
                session,
                attachment_id,
                meeting_id,
                f"Unexpected extraction failure: {exc}",
            )
            return

        sidecar_mod.write_attachment_sidecar(
            storage_mod.sidecar_path(data_dir, meeting_id, attachment_id),
            frontmatter={
                "attachment_id": attachment_id,
                "meeting_id": meeting_id,
                "kind": row.kind,
                "title": row.title,
                "source": row.original_filename or "",
                "extracted_at": datetime.now(timezone.utc).isoformat(),
                "extraction_method": method,
                "summary_status": "pending",
            },
            extracted=extracted,
            summary="",
        )

        # Until the summarizer batch lands, "ready" means "extracted, no
        # summary yet". The minutes-generation injection (next batch)
        # treats summary-empty rows as injection-skipped, so this is
        # forward-compatible.
        storage_mod.update_status(session, attachment_id, "ready")
        session.commit()
        _LOG.info("Attachment %s extracted via %s", attachment_id, method)
    finally:
        session.close()


def _set_error(session, attachment_id: str, meeting_id: str, message: str) -> None:
    """Flip the row to ``status='error'``, write a stub sidecar, log."""
    _LOG.warning("Attachment %s failed: %s", attachment_id, message)
    storage_mod.update_status(session, attachment_id, "error", error=message)
    session.commit()


def schedule(config: AppConfig, attachment_id: str) -> asyncio.Task:
    """Fire-and-forget wrapper for production callers.

    Returns the task so callers (and tests) can await it; production
    callers ignore the return value. Mirrors ``schedule_background_update``
    in :mod:`meeting_minutes.external_notes`.
    """
    return asyncio.create_task(process_attachment(config, attachment_id))
