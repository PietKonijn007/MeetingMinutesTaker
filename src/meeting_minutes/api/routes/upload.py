"""Upload external transcript endpoint."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from meeting_minutes.api.deps import get_config
from meeting_minutes.config import AppConfig

router = APIRouter(tags=["upload"])


class UploadResponse(BaseModel):
    meeting_id: str
    status: str = "processing"
    message: str = ""


@router.post("/api/upload-transcript", response_model=UploadResponse)
async def upload_transcript(
    config: Annotated[AppConfig, Depends(get_config)],
    file: UploadFile = File(...),
    title: str = Form("Uploaded Meeting"),
    date: str = Form(...),  # YYYY-MM-DD
    time: str = Form(""),   # HH:MM (optional)
    attendees: str = Form(""),  # comma-separated
    meeting_type: str = Form(""),  # empty = auto-detect
    language: str = Form("en"),
):
    """Upload an external transcript file and generate meeting minutes."""
    from meeting_minutes.api.routes.recording import (
        _ensure_pipeline_worker,
        _pipeline_jobs,
        _pipeline_queue,
    )
    from meeting_minutes.system2.transcript_parser import parse_uploaded_transcript

    # Read file content
    try:
        raw = await file.read()
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            content = raw.decode("latin-1")
        except Exception:
            raise HTTPException(
                status_code=400,
                detail="Could not decode file. Please upload a UTF-8 text file.",
            )

    if not content.strip():
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    # Parse attendees
    attendee_list = [a.strip() for a in attendees.split(",") if a.strip()] if attendees else []

    # Parse the file into TranscriptJSON
    try:
        transcript_json = parse_uploaded_transcript(
            content=content,
            filename=file.filename or "transcript.txt",
            title=title,
            date=date,
            time_str=time,
            attendees=attendee_list,
            meeting_type=meeting_type or "other",
            language=language,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to parse transcript: {exc}")

    meeting_id = transcript_json.meeting_id

    # Save transcript JSON to disk
    data_dir = Path(config.data_dir).expanduser()
    transcripts_dir = data_dir / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)

    transcript_path = transcripts_dir / f"{meeting_id}.json"
    transcript_path.write_text(
        transcript_json.model_dump_json(indent=2),
        encoding="utf-8",
    )

    print(f"\n  Uploaded transcript saved: {transcript_path.name}")
    print(f"  Title: {title} | Date: {date} | Attendees: {len(attendee_list)}")
    print(f"  Text length: {len(content):,} chars | Segments: {len(transcript_json.transcript.get('segments', []))}")

    # Enqueue pipeline job (generation + ingestion)
    _pipeline_jobs[meeting_id] = {
        "step": "queued",
        "progress": 0.0,
        "error": None,
        "started_at": time.time(),
    }
    _pipeline_queue.put_nowait((meeting_id, config))
    _ensure_pipeline_worker()

    return UploadResponse(
        meeting_id=meeting_id,
        status="processing",
        message=f"Transcript uploaded. Pipeline processing started for '{title}'.",
    )
