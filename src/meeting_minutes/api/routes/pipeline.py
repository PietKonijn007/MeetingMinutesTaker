"""Pipeline state API endpoints (PIP-1)."""

from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from meeting_minutes.api.deps import get_config, get_db_session
from meeting_minutes.config import AppConfig
from meeting_minutes.pipeline_state import (
    Stage,
    Status,
    get_stages,
    next_stage,
)
from meeting_minutes.system3.db import PipelineStageORM


router = APIRouter(tags=["pipeline"])


class StageStateResponse(BaseModel):
    meeting_id: str
    stage: str
    status: str
    attempt: int
    started_at: str | None = None
    finished_at: str | None = None
    last_error: str | None = None
    last_error_at: str | None = None
    artifact_path: str | None = None


class ResumeRequest(BaseModel):
    from_stage: Optional[str] = None


@router.get("/api/meetings/{meeting_id}/pipeline", response_model=list[StageStateResponse])
def get_pipeline_state(
    meeting_id: str,
    session: Annotated[Session, Depends(get_db_session)],
):
    """List all recorded pipeline stage states for a meeting."""
    states = get_stages(session, meeting_id)
    return [
        StageStateResponse(
            meeting_id=s.meeting_id,
            stage=s.stage.value,
            status=s.status.value,
            attempt=s.attempt,
            started_at=s.started_at.isoformat() if s.started_at else None,
            finished_at=s.finished_at.isoformat() if s.finished_at else None,
            last_error=s.last_error,
            last_error_at=s.last_error_at.isoformat() if s.last_error_at else None,
            artifact_path=s.artifact_path,
        )
        for s in states
    ]


async def _run_resume(config: AppConfig, meeting_id: str, from_stage: Stage | None):
    from meeting_minutes.pipeline import PipelineOrchestrator

    orchestrator = PipelineOrchestrator(config)
    try:
        await orchestrator.resume_from(meeting_id, from_stage=from_stage)
    except Exception:
        # State rows already record the failure; the background task itself
        # swallows the error so FastAPI doesn't log a second stack.
        pass


@router.post("/api/meetings/{meeting_id}/resume", status_code=202)
async def resume_meeting(
    meeting_id: str,
    body: ResumeRequest,
    background_tasks: BackgroundTasks,
    config: Annotated[AppConfig, Depends(get_config)],
    session: Annotated[Session, Depends(get_db_session)],
):
    """Kick off pipeline resume in the background. Returns a 202 + job ref."""
    states = get_stages(session, meeting_id)
    if not states and next_stage(session, meeting_id) is None:
        raise HTTPException(status_code=404, detail=f"No pipeline state for {meeting_id}")

    from_stage: Stage | None = None
    if body.from_stage:
        try:
            from_stage = Stage(body.from_stage)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Unknown stage: {body.from_stage}")

    background_tasks.add_task(_run_resume, config, meeting_id, from_stage)
    return {
        "status": "accepted",
        "meeting_id": meeting_id,
        "job_ref": f"resume:{meeting_id}",
        "from_stage": from_stage.value if from_stage else None,
    }


@router.get("/api/pipeline/interrupted")
def list_interrupted(
    session: Annotated[Session, Depends(get_db_session)],
):
    """List every meeting with any ``failed`` or ``pending`` stage."""
    rows = (
        session.query(PipelineStageORM)
        .filter(PipelineStageORM.status.in_([Status.FAILED.value, Status.PENDING.value]))
        .all()
    )
    by_meeting: dict[str, list[dict]] = {}
    for row in rows:
        by_meeting.setdefault(row.meeting_id, []).append({
            "stage": row.stage,
            "status": row.status,
            "attempt": row.attempt,
            "last_error": row.last_error,
            "last_error_at": row.last_error_at.isoformat() if row.last_error_at else None,
        })
    return {
        "meetings": [
            {"meeting_id": mid, "stages": stages}
            for mid, stages in by_meeting.items()
        ],
        "count": len(by_meeting),
    }
