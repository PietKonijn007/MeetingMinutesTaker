"""Meeting endpoints."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from meeting_minutes.api.deps import get_config, get_db_session, get_search, get_storage
from meeting_minutes.api.schemas import (
    ActionItemResponse,
    DecisionResponse,
    ErrorResponse,
    ExportRequest,
    MeetingDetail,
    MeetingListItem,
    MeetingUpdate,
    MinutesResponse,
    PaginatedResponse,
    PersonResponse,
    TranscriptResponse,
)
from meeting_minutes.config import AppConfig
from meeting_minutes.models import SearchQuery
from meeting_minutes.system3.db import MeetingORM
from meeting_minutes.system3.search import SearchEngine
from meeting_minutes.system3.storage import MeetingFilters, StorageEngine

router = APIRouter(prefix="/api/meetings", tags=["meetings"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _meeting_to_list_item(m: MeetingORM) -> MeetingListItem:
    return MeetingListItem(
        meeting_id=m.meeting_id,
        title=m.title,
        date=m.date.isoformat() if m.date else None,
        meeting_type=m.meeting_type,
        duration=m.duration,
        organizer=m.organizer,
        summary=m.minutes.summary if m.minutes else None,
        attendee_names=[a.name for a in m.attendees],
        action_item_count=len(m.action_items),
        decision_count=len(m.decisions),
    )


def _meeting_to_detail(m: MeetingORM) -> MeetingDetail:
    attendees = [
        PersonResponse(person_id=a.person_id, name=a.name, email=a.email)
        for a in m.attendees
    ]
    minutes = None
    if m.minutes:
        minutes = MinutesResponse(
            minutes_id=m.minutes.minutes_id,
            summary=m.minutes.summary,
            markdown_content=m.minutes.markdown_content,
            generated_at=m.minutes.generated_at.isoformat() if m.minutes.generated_at else None,
            llm_model=m.minutes.llm_model,
            review_status=m.minutes.review_status,
        )
    action_items = [
        ActionItemResponse(
            action_item_id=ai.action_item_id,
            description=ai.description,
            owner=ai.owner,
            due_date=ai.due_date,
            status=ai.status or "open",
            meeting_id=m.meeting_id,
            meeting_title=m.title,
        )
        for ai in m.action_items
    ]
    decisions = [
        DecisionResponse(
            decision_id=d.decision_id,
            description=d.description,
            made_by=d.made_by,
            mentioned_at_seconds=d.mentioned_at_seconds,
            meeting_id=m.meeting_id,
            meeting_title=m.title,
            meeting_date=m.date.isoformat() if m.date else None,
        )
        for d in m.decisions
    ]
    transcript_text = m.transcript.full_text if m.transcript else None
    audio_file_path = m.transcript.audio_file_path if m.transcript else None

    return MeetingDetail(
        meeting_id=m.meeting_id,
        title=m.title,
        date=m.date.isoformat() if m.date else None,
        meeting_type=m.meeting_type,
        duration=m.duration,
        organizer=m.organizer,
        status=m.status,
        attendees=attendees,
        minutes=minutes,
        action_items=action_items,
        decisions=decisions,
        transcript_text=transcript_text,
        audio_file_path=audio_file_path,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=PaginatedResponse)
def list_meetings(
    storage: Annotated[StorageEngine, Depends(get_storage)],
    search_engine: Annotated[SearchEngine, Depends(get_search)],
    session: Annotated[Session, Depends(get_db_session)],
    q: Optional[str] = Query(None, description="Full-text search query"),
    type: Optional[str] = Query(None, alias="type", description="Meeting type filter"),
    after: Optional[str] = Query(None, description="After date (ISO)"),
    before: Optional[str] = Query(None, description="Before date (ISO)"),
    person: Optional[str] = Query(None, description="Attendee email filter"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """List meetings with optional filtering and search."""
    if q:
        # Use SearchEngine for full-text queries
        parsed = search_engine.parse_query(q)
        if type:
            parsed.meeting_type = type
        if after:
            try:
                parsed.after_date = datetime.fromisoformat(after)
            except ValueError:
                pass
        if before:
            try:
                parsed.before_date = datetime.fromisoformat(before)
            except ValueError:
                pass
        parsed.limit = limit
        parsed.offset = offset

        results = search_engine.search(parsed)

        # Hydrate ORM objects for rich response
        items: list[MeetingListItem] = []
        for r in results.results:
            m = storage.get_meeting(r.meeting_id)
            if m:
                item = _meeting_to_list_item(m)
                # Override summary with search snippet when available
                if r.snippet:
                    item.summary = r.snippet
                items.append(item)

        return PaginatedResponse(
            items=[i.model_dump() for i in items],
            total=results.total_count,
            limit=limit,
            offset=offset,
        )

    # No search query — use StorageEngine
    after_dt = None
    before_dt = None
    if after:
        try:
            after_dt = datetime.fromisoformat(after)
        except ValueError:
            pass
    if before:
        try:
            before_dt = datetime.fromisoformat(before)
        except ValueError:
            pass

    filters = MeetingFilters(
        meeting_type=type,
        after_date=after_dt,
        before_date=before_dt,
        attendee=person,
    )
    meetings = storage.list_meetings(limit=limit, offset=offset, filters=filters)

    # Total count (without limit/offset)
    total_query = session.query(MeetingORM)
    if type:
        total_query = total_query.filter(MeetingORM.meeting_type == type)
    if after_dt:
        total_query = total_query.filter(MeetingORM.date >= after_dt)
    if before_dt:
        total_query = total_query.filter(MeetingORM.date <= before_dt)
    total = total_query.count()

    items = [_meeting_to_list_item(m) for m in meetings]
    return PaginatedResponse(
        items=[i.model_dump() for i in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{meeting_id}", response_model=MeetingDetail)
def get_meeting(
    meeting_id: str,
    storage: Annotated[StorageEngine, Depends(get_storage)],
):
    """Get full meeting detail."""
    m = storage.get_meeting(meeting_id)
    if m is None:
        raise HTTPException(status_code=404, detail=f"No meeting with ID {meeting_id}")
    return _meeting_to_detail(m)


@router.get("/{meeting_id}/transcript", response_model=TranscriptResponse)
def get_transcript(
    meeting_id: str,
    storage: Annotated[StorageEngine, Depends(get_storage)],
):
    """Get full transcript for a meeting."""
    m = storage.get_meeting(meeting_id)
    if m is None:
        raise HTTPException(status_code=404, detail=f"No meeting with ID {meeting_id}")
    if m.transcript is None:
        raise HTTPException(status_code=404, detail="No transcript available for this meeting")

    return TranscriptResponse(
        meeting_id=meeting_id,
        full_text=m.transcript.full_text,
        language=m.transcript.language,
        audio_file_path=m.transcript.audio_file_path,
    )


@router.get("/{meeting_id}/audio")
def get_audio(
    meeting_id: str,
    storage: Annotated[StorageEngine, Depends(get_storage)],
):
    """Stream the audio file for a meeting."""
    m = storage.get_meeting(meeting_id)
    if m is None:
        raise HTTPException(status_code=404, detail=f"No meeting with ID {meeting_id}")
    if m.transcript is None or not m.transcript.audio_file_path:
        raise HTTPException(status_code=404, detail="No audio file available for this meeting")

    audio_path = Path(m.transcript.audio_file_path)
    if not audio_path.exists():
        raise HTTPException(status_code=404, detail="Audio file not found on disk")

    media_type = "audio/flac"
    if audio_path.suffix == ".wav":
        media_type = "audio/wav"
    elif audio_path.suffix == ".mp3":
        media_type = "audio/mpeg"
    elif audio_path.suffix in (".m4a", ".mp4"):
        media_type = "audio/mp4"

    return FileResponse(
        path=str(audio_path),
        media_type=media_type,
        filename=audio_path.name,
    )


@router.patch("/{meeting_id}", response_model=MeetingDetail)
def update_meeting(
    meeting_id: str,
    body: MeetingUpdate,
    storage: Annotated[StorageEngine, Depends(get_storage)],
    session: Annotated[Session, Depends(get_db_session)],
):
    """Update meeting metadata (status, tags)."""
    m = storage.get_meeting(meeting_id)
    if m is None:
        raise HTTPException(status_code=404, detail=f"No meeting with ID {meeting_id}")

    if body.status is not None:
        m.status = body.status

    session.commit()
    session.refresh(m)
    return _meeting_to_detail(m)


@router.delete("/{meeting_id}")
def delete_meeting(
    meeting_id: str,
    storage: Annotated[StorageEngine, Depends(get_storage)],
    search_engine: Annotated[SearchEngine, Depends(get_search)],
):
    """Delete a meeting and all associated data."""
    search_engine.remove_from_index(meeting_id)
    ok = storage.delete_meeting(meeting_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"No meeting with ID {meeting_id}")
    return {"status": "deleted", "meeting_id": meeting_id}


@router.post("/{meeting_id}/regenerate")
async def regenerate_meeting(
    meeting_id: str,
    storage: Annotated[StorageEngine, Depends(get_storage)],
    config: Annotated[AppConfig, Depends(get_config)],
):
    """Re-run minutes generation for a meeting."""
    m = storage.get_meeting(meeting_id)
    if m is None:
        raise HTTPException(status_code=404, detail=f"No meeting with ID {meeting_id}")

    from meeting_minutes.pipeline import PipelineOrchestrator

    orchestrator = PipelineOrchestrator(config)
    try:
        await orchestrator.reprocess(meeting_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Regeneration failed: {exc}")

    return {"status": "regenerated", "meeting_id": meeting_id}


@router.post("/{meeting_id}/export")
def export_meeting(
    meeting_id: str,
    body: ExportRequest,
    storage: Annotated[StorageEngine, Depends(get_storage)],
    config: Annotated[AppConfig, Depends(get_config)],
):
    """Export meeting minutes in the requested format."""
    m = storage.get_meeting(meeting_id)
    if m is None:
        raise HTTPException(status_code=404, detail=f"No meeting with ID {meeting_id}")

    if body.format not in ("pdf", "md"):
        raise HTTPException(status_code=400, detail="Unsupported format. Use 'pdf' or 'md'.")

    if m.minutes is None or not m.minutes.markdown_content:
        raise HTTPException(status_code=400, detail="No minutes to export for this meeting")

    if body.format == "md":
        data_dir = Path(config.data_dir).expanduser()
        export_dir = data_dir / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{meeting_id}.md"
        export_path = export_dir / filename
        export_path.write_text(m.minutes.markdown_content, encoding="utf-8")
        return FileResponse(
            path=str(export_path),
            media_type="text/markdown",
            filename=filename,
        )

    # PDF export — basic placeholder that returns markdown wrapped in a note
    # A full PDF implementation would use a library like weasyprint or reportlab.
    raise HTTPException(
        status_code=501,
        detail="PDF export is not yet implemented. Use 'md' format instead.",
    )
