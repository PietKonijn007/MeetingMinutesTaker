"""Action item endpoints."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from meeting_minutes.api.deps import get_db_session, get_storage
from meeting_minutes.api.schemas import ActionItemResponse, ActionItemUpdate, PaginatedResponse
from meeting_minutes.system3.db import ActionItemORM, MeetingORM
from meeting_minutes.system3.storage import ActionItemFilters, StorageEngine

router = APIRouter(prefix="/api/action-items", tags=["action-items"])


@router.get("", response_model=PaginatedResponse)
def list_action_items(
    storage: Annotated[StorageEngine, Depends(get_storage)],
    session: Annotated[Session, Depends(get_db_session)],
    owner: Optional[str] = Query(None, description="Filter by owner name"),
    status: Optional[str] = Query(None, description="Filter by status (open, done, all)"),
    overdue: Optional[bool] = Query(None, description="Show only overdue items"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List all action items with optional filters."""
    filters = ActionItemFilters(
        owner=owner,
        status=status if status and status != "all" else None,
        overdue=overdue or False,
    )
    items = storage.get_action_items(filters)

    # Apply overdue filter manually (check due_date < today)
    if overdue:
        today_str = date.today().isoformat()
        items = [
            ai for ai in items
            if ai.due_date and ai.due_date < today_str and ai.status != "done"
        ]

    total = len(items)
    page = items[offset : offset + limit]

    response_items = []
    for ai in page:
        # Get meeting title via relationship
        meeting_title = None
        if ai.meeting and ai.meeting.title:
            meeting_title = ai.meeting.title
        elif ai.meeting_id:
            m = session.get(MeetingORM, ai.meeting_id)
            if m:
                meeting_title = m.title

        response_items.append(
            ActionItemResponse(
                action_item_id=ai.action_item_id,
                description=ai.description,
                owner=ai.owner,
                due_date=ai.due_date,
                status=ai.status or "open",
                meeting_id=ai.meeting_id,
                meeting_title=meeting_title,
            ).model_dump()
        )

    return PaginatedResponse(
        items=response_items,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.patch("/{action_item_id}", response_model=ActionItemResponse)
def update_action_item(
    action_item_id: str,
    body: ActionItemUpdate,
    storage: Annotated[StorageEngine, Depends(get_storage)],
    session: Annotated[Session, Depends(get_db_session)],
):
    """Update an action item's status."""
    ok = storage.update_action_item_status(action_item_id, body.status)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Action item {action_item_id} not found")

    ai = session.get(ActionItemORM, action_item_id)
    meeting_title = None
    if ai and ai.meeting_id:
        m = session.get(MeetingORM, ai.meeting_id)
        if m:
            meeting_title = m.title

    return ActionItemResponse(
        action_item_id=ai.action_item_id,
        description=ai.description,
        owner=ai.owner,
        due_date=ai.due_date,
        status=ai.status or "open",
        meeting_id=ai.meeting_id,
        meeting_title=meeting_title,
    )
