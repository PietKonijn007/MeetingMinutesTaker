"""Async worker — extract → summarize → write sidecar.

Two LLM-independent phases (load row, extract text) plus one LLM phase
(summarize). The summarizer is fired in its own ``asyncio.to_thread``
call so the event loop stays responsive while the LLM call is in flight
— important during recording, where the websocket can't be starved.

Fired from the API handler on every successful upload via
``asyncio.create_task``. Must never raise — failures are recorded on the
DB row's ``status='error'`` + ``error`` column and on the sidecar
frontmatter, so the UI can surface them.

Status timeline:
    pending → extracting → summarizing → ready
                  ↘                 ↘
                   error             error  (error string set on row + sidecar)

Any exception during summarization downgrades to a "ready" sidecar with
an empty summary plus ``summary_status: error`` on the frontmatter — the
extracted text is still useful for the user, and minutes generation
will simply skip injection for this attachment.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from meeting_minutes.attachments import sidecar as sidecar_mod
from meeting_minutes.attachments import storage as storage_mod
from meeting_minutes.attachments.extractors import (
    ExtractionError,
    extract,
    extract_link,
)
from meeting_minutes.attachments.summarizer import (
    SummaryRequest,
    summarize_attachment,
)
from meeting_minutes.config import AppConfig, resolve_db_path
from meeting_minutes.system2.llm_client import LLMClient
from meeting_minutes.system3.db import AttachmentORM, get_session_factory

_LOG = logging.getLogger(__name__)


async def process_attachment(
    config: AppConfig,
    attachment_id: str,
    *,
    session_factory: Callable[[], object] | None = None,
    llm_client: LLMClient | None = None,
) -> None:
    """End-to-end: load row → extract → write sidecar → summarize → mark ready.

    ``session_factory`` and ``llm_client`` are injectable so tests can
    drop in fakes. Production callers leave both ``None``; we build them
    from ``config``.
    """
    if session_factory is None:
        db_path = resolve_db_path(config.storage.sqlite_path)
        session_factory = get_session_factory(f"sqlite:///{db_path}")

    # Phase 1: extraction (CPU/IO-bound) runs in a thread so it doesn't
    # stall the event loop while a 50 MB PDF parses.
    extracted = await asyncio.to_thread(
        _run_extraction, config, attachment_id, session_factory
    )
    if extracted is None:
        # Extraction either failed or the row vanished; the helper has
        # already written the error state. Nothing more to do.
        return

    # Phase 2: summarization runs as its own LLM call. ``LLMClient`` is
    # already async, so no thread offload needed for the API leg —
    # we just await directly.
    if llm_client is None:
        llm_client = LLMClient(config.generation.llm)
    await _run_summary(
        config=config,
        session_factory=session_factory,
        attachment_id=attachment_id,
        llm_client=llm_client,
        extracted_text=extracted.extracted_text,
        extraction_method=extracted.extraction_method,
        title=extracted.title,
        caption=extracted.caption,
        source=extracted.source,
    )


class _ExtractionResult:
    """Internal value object passed from the extraction thread to the async
    summarization step. A class (not a dict) so type-checking catches typos.
    """

    __slots__ = ("extracted_text", "extraction_method", "title", "caption", "source")

    def __init__(
        self,
        extracted_text: str,
        extraction_method: str,
        title: str,
        caption: str | None,
        source: str,
    ) -> None:
        self.extracted_text = extracted_text
        self.extraction_method = extraction_method
        self.title = title
        self.caption = caption
        self.source = source


def _run_extraction(
    config: AppConfig, attachment_id: str, session_factory: Callable
) -> _ExtractionResult | None:
    """Blocking phase 1: load row, run extractor, write sidecar with empty summary.

    Returns ``None`` if the row vanished or extraction failed (in which
    case the error is already recorded). Returns the extracted text +
    metadata otherwise so the caller can run the summarizer.
    """
    data_dir = Path(config.data_dir).expanduser()
    session = session_factory()
    try:
        row = session.get(AttachmentORM, attachment_id)
        if row is None:
            _LOG.warning("Attachment %s vanished before worker ran", attachment_id)
            return None

        meeting_id = row.meeting_id

        storage_mod.update_status(session, attachment_id, "extracting")
        session.commit()

        try:
            if row.kind == "link":
                extracted, method = _extract_link_for_row(row)
                source_label = row.url or ""
            else:
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
                    return None
                extracted, method = extract(original, row.mime_type)
                source_label = row.original_filename or ""
        except ExtractionError as exc:
            _set_error(session, attachment_id, meeting_id, str(exc))
            return None
        except Exception as exc:  # noqa: BLE001
            _LOG.exception("Unexpected extraction failure for %s", attachment_id)
            _set_error(
                session,
                attachment_id,
                meeting_id,
                f"Unexpected extraction failure: {exc}",
            )
            return None

        sidecar_mod.write_attachment_sidecar(
            storage_mod.sidecar_path(data_dir, meeting_id, attachment_id),
            frontmatter={
                "attachment_id": attachment_id,
                "meeting_id": meeting_id,
                "kind": row.kind,
                "title": row.title,
                "source": source_label,
                "extracted_at": datetime.now(timezone.utc).isoformat(),
                "extraction_method": method,
                "summary_status": "pending",
            },
            extracted=extracted,
            summary="",
        )

        storage_mod.update_status(session, attachment_id, "summarizing")
        session.commit()
        _LOG.info("Attachment %s extracted via %s; queueing summarizer", attachment_id, method)
        return _ExtractionResult(
            extracted_text=extracted,
            extraction_method=method,
            title=row.title,
            caption=row.caption,
            source=source_label,
        )
    finally:
        session.close()


def _extract_link_for_row(row: AttachmentORM) -> tuple[str, str]:
    """Run the link extractor + opportunistically improve the row's title.

    The page ``<title>`` is a much better default than the raw URL,
    but only when the user didn't already pick something. Caller's
    session commits the title change as part of the same transaction
    that flips status to ``summarizing``.
    """
    if not row.url:
        raise ExtractionError("Link attachment has no URL set")

    text, method, metadata = extract_link(row.url)
    page_title = (metadata.get("page_title") or "").strip()
    if page_title and (not row.title or row.title == row.url):
        row.title = page_title
    return text, method


async def _run_summary(
    *,
    config: AppConfig,
    session_factory: Callable,
    attachment_id: str,
    llm_client: LLMClient,
    extracted_text: str,
    extraction_method: str,
    title: str,
    caption: str | None,
    source: str,
) -> None:
    """Phase 2: call the LLM, write the summary into the sidecar, mark ready.

    Failure here doesn't poison the row: the extracted text is already
    on disk and useful for the user. We mark ``status='ready'`` (the
    extraction succeeded) but flip the sidecar's ``summary_status`` to
    ``error`` so minutes generation skips injection and the UI can
    surface the failure inline with the attachment.
    """
    data_dir = Path(config.data_dir).expanduser()
    sidecar_path = storage_mod.sidecar_path(
        data_dir, _meeting_id_for(session_factory, attachment_id), attachment_id
    )

    try:
        result = await summarize_attachment(
            llm_client,
            SummaryRequest(
                title=title,
                caption=caption,
                source=source,
                extraction_method=extraction_method,
                extracted_text=extracted_text,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        _LOG.exception("Summarizer failed for %s", attachment_id)
        sidecar_mod.update_summary(
            sidecar_path,
            summary="",
            summary_status="error",
            error=str(exc),
        )
        # Row stays at status='ready' since extraction succeeded; the
        # summary-level error is on the sidecar where the UI reads it.
        session = session_factory()
        try:
            storage_mod.update_status(session, attachment_id, "ready")
            session.commit()
        finally:
            session.close()
        return

    sidecar_mod.update_summary(
        sidecar_path,
        summary=result.summary_markdown,
        summary_status="ready",
        summary_target=result.tier.value,
    )

    session = session_factory()
    try:
        storage_mod.update_status(session, attachment_id, "ready")
        session.commit()
    finally:
        session.close()
    _LOG.info(
        "Attachment %s summarized (tier=%s, truncated=%s)",
        attachment_id,
        result.tier.value,
        result.truncated,
    )


def _meeting_id_for(session_factory: Callable, attachment_id: str) -> str:
    """Look up the meeting_id for an attachment in its own short-lived session.

    We can't pass the row across the async boundary because SQLAlchemy
    objects are bound to a session that's already closed by the time
    summarization starts.
    """
    session = session_factory()
    try:
        row = session.get(AttachmentORM, attachment_id)
        return row.meeting_id if row else ""
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
