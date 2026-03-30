"""Decision endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from meeting_minutes.api.deps import get_db_session
from meeting_minutes.api.schemas import DecisionResponse, PaginatedResponse
from meeting_minutes.system3.db import DecisionORM, MeetingORM

router = APIRouter(prefix="/api/decisions", tags=["decisions"])


@router.get("", response_model=PaginatedResponse)
def list_decisions(
    session: Annotated[Session, Depends(get_db_session)],
    after: Optional[str] = Query(None, description="After date (ISO)"),
    before: Optional[str] = Query(None, description="Before date (ISO)"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List all decisions with optional date filters."""
    query = (
        session.query(DecisionORM)
        .join(MeetingORM, DecisionORM.meeting_id == MeetingORM.meeting_id)
    )

    if after:
        try:
            after_dt = datetime.fromisoformat(after)
            query = query.filter(MeetingORM.date >= after_dt)
        except ValueError:
            pass

    if before:
        try:
            before_dt = datetime.fromisoformat(before)
            query = query.filter(MeetingORM.date <= before_dt)
        except ValueError:
            pass

    total = query.count()
    decisions = query.order_by(MeetingORM.date.desc()).offset(offset).limit(limit).all()

    items = []
    for d in decisions:
        meeting = session.get(MeetingORM, d.meeting_id)
        items.append(
            DecisionResponse(
                decision_id=d.decision_id,
                description=d.description,
                made_by=d.made_by,
                mentioned_at_seconds=d.mentioned_at_seconds,
                meeting_id=d.meeting_id,
                meeting_title=meeting.title if meeting else None,
                meeting_date=meeting.date.isoformat() if meeting and meeting.date else None,
            ).model_dump()
        )

    return PaginatedResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )
