"""Retention policy API endpoints."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from meeting_minutes.api.deps import get_config, get_db_session
from meeting_minutes.config import AppConfig

router = APIRouter(prefix="/api/retention", tags=["retention"])


class BulkDeleteRequest(BaseModel):
    meeting_ids: list[str]


@router.post("/cleanup")
def run_retention_cleanup(
    config: Annotated[AppConfig, Depends(get_config)],
):
    """Enforce retention policies — delete old files."""
    from meeting_minutes.retention import enforce_retention

    deleted = enforce_retention(config)
    return {"deleted": deleted, "total": sum(deleted.values())}


@router.get("/status")
def retention_status(
    config: Annotated[AppConfig, Depends(get_config)],
):
    """Return current retention config, file counts, and oldest file ages."""
    from meeting_minutes.retention import get_retention_status

    return get_retention_status(config)


@router.get("/oldest-audio")
def oldest_audio(
    session: Annotated[Session, Depends(get_db_session)],
    config: Annotated[AppConfig, Depends(get_config)],
    limit: int = 20,
):
    """Return the oldest audio files whose pipeline has reached a terminal state.

    Used by the DSK-1 warning modal to offer safe, bulk manual cleanup.
    Meetings whose pipeline has NOT reached a terminal state are excluded
    — their audio is still needed for resume.
    """
    from meeting_minutes.pipeline_state import has_terminal_state
    from meeting_minutes.system3.db import TranscriptORM

    rows = (
        session.query(TranscriptORM.meeting_id, TranscriptORM.audio_file_path)
        .filter(TranscriptORM.audio_file_path.isnot(None))
        .all()
    )

    eligible: list[dict] = []
    for meeting_id, audio_path in rows:
        if not audio_path:
            continue
        path = Path(audio_path).expanduser()
        if not path.exists():
            continue
        if not has_terminal_state(session, meeting_id):
            continue
        try:
            stat = path.stat()
            eligible.append({
                "meeting_id": meeting_id,
                "audio_file_path": str(path),
                "size_bytes": stat.st_size,
                "mtime": stat.st_mtime,
            })
        except Exception:
            continue

    eligible.sort(key=lambda e: e["mtime"])
    return {"files": eligible[:limit], "total_eligible": len(eligible)}


@router.delete("/audio")
def delete_audio_bulk(
    body: BulkDeleteRequest,
    session: Annotated[Session, Depends(get_db_session)],
    config: Annotated[AppConfig, Depends(get_config)],
):
    """Delete audio files for the given meeting IDs (only if pipeline is terminal)."""
    from meeting_minutes.pipeline_state import has_terminal_state
    from meeting_minutes.system3.db import TranscriptORM

    if not body.meeting_ids:
        raise HTTPException(status_code=400, detail="meeting_ids must not be empty")

    deleted: list[str] = []
    skipped: list[dict] = []

    for mid in body.meeting_ids:
        transcript = session.get(TranscriptORM, mid)
        if transcript is None or not transcript.audio_file_path:
            skipped.append({"meeting_id": mid, "reason": "no audio"})
            continue
        if not has_terminal_state(session, mid):
            skipped.append({"meeting_id": mid, "reason": "pipeline not terminal"})
            continue
        path = Path(transcript.audio_file_path).expanduser()
        try:
            if path.exists():
                path.unlink()
            deleted.append(mid)
        except Exception as exc:
            skipped.append({"meeting_id": mid, "reason": str(exc)})

    return {"deleted": deleted, "skipped": skipped}
