"""Action item endpoints."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from meeting_minutes.action_review import resync_action_items_artifacts
from meeting_minutes.api.deps import get_config, get_db_session, get_storage
from meeting_minutes.api.schemas import (
    ActionItemResponse,
    ActionItemUpdate,
    BulkActionReviewRequest,
    BulkConfirmBeforeRequest,
    PaginatedResponse,
)
from meeting_minutes.config import AppConfig
from meeting_minutes.models import ActionItemProposalState
from meeting_minutes.system3.db import ActionItemORM, MeetingORM
from meeting_minutes.system3.storage import ActionItemFilters, StorageEngine

router = APIRouter(prefix="/api/action-items", tags=["action-items"])


def _to_response(ai: ActionItemORM, m: MeetingORM | None = None) -> ActionItemResponse:
    meeting_title = None
    meeting_date = None
    if m is not None:
        meeting_title = m.title
        meeting_date = m.date.isoformat() if m.date else None
    return ActionItemResponse(
        action_item_id=ai.action_item_id,
        description=ai.description,
        owner=ai.owner,
        due_date=ai.due_date,
        status=ai.status or "open",
        proposal_state=ai.proposal_state or ActionItemProposalState.PROPOSED.value,
        meeting_id=ai.meeting_id,
        meeting_title=meeting_title,
        meeting_date=meeting_date,
    )


@router.get("", response_model=PaginatedResponse)
def list_action_items(
    storage: Annotated[StorageEngine, Depends(get_storage)],
    session: Annotated[Session, Depends(get_db_session)],
    owner: Optional[str] = Query(None, description="Filter by owner name"),
    status: Optional[str] = Query(None, description="Filter by status (open, done, all)"),
    overdue: Optional[bool] = Query(None, description="Show only overdue items"),
    proposal_state: Optional[str] = Query(
        None,
        description=(
            "Filter by review state. One of: proposed, confirmed, rejected, all. "
            "Defaults to 'confirmed' so the global tracker stays clean."
        ),
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List all action items with optional filters.

    By default, returns only confirmed items — proposed/rejected ones live
    inside the per-meeting review flow and are excluded from the global
    tracker. Pass ``proposal_state=proposed``/``rejected`` to surface those
    explicitly, or ``proposal_state=all`` to disable the filter entirely.
    """
    if proposal_state is None or proposal_state == "":
        ps_filter: str | None = ActionItemProposalState.CONFIRMED.value
    elif proposal_state == "all":
        ps_filter = None
    else:
        ps_filter = proposal_state

    filters = ActionItemFilters(
        owner=owner,
        status=status if status and status != "all" else None,
        overdue=overdue or False,
        proposal_state=ps_filter,
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
        m = ai.meeting if ai.meeting else (
            session.get(MeetingORM, ai.meeting_id) if ai.meeting_id else None
        )
        response_items.append(_to_response(ai, m).model_dump())

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
    config: Annotated[AppConfig, Depends(get_config)],
):
    """Update an action item.

    Supports partial updates: status (post-confirmation lifecycle),
    proposal_state (review flow), and inline edits to description/owner/
    due_date. Re-renders the meeting's markdown + Obsidian export when the
    change could affect them.
    """
    item = storage.update_action_item(
        action_item_id,
        status=body.status,
        proposal_state=body.proposal_state,
        description=body.description,
        owner=body.owner,
        due_date=body.due_date,
    )
    if item is None:
        raise HTTPException(status_code=404, detail=f"Action item {action_item_id} not found")

    # Any of these mutations can change what shows up in the rendered minutes
    # / Obsidian export, so resync the artifacts. resync is best-effort.
    if (
        body.proposal_state is not None
        or body.description is not None
        or body.owner is not None
        or body.due_date is not None
        or body.status is not None
    ) and item.meeting_id:
        try:
            resync_action_items_artifacts(
                session=session, config=config, meeting_id=item.meeting_id,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠ resync after PATCH failed: {exc}")

    m = session.get(MeetingORM, item.meeting_id) if item.meeting_id else None
    return _to_response(item, m)


@router.post(
    "/bulk-review/{meeting_id}",
    response_model=PaginatedResponse,
)
def bulk_review_action_items(
    meeting_id: str,
    body: BulkActionReviewRequest,
    storage: Annotated[StorageEngine, Depends(get_storage)],
    session: Annotated[Session, Depends(get_db_session)],
    config: Annotated[AppConfig, Depends(get_config)],
):
    """Confirm/reject a batch of proposed action items on one meeting.

    Returns the updated set of action items for the meeting (all proposal
    states) so the UI can update its row list in one round-trip.
    """
    m = session.get(MeetingORM, meeting_id)
    if m is None:
        raise HTTPException(status_code=404, detail=f"No meeting with ID {meeting_id}")

    confirmed, rejected = storage.bulk_review_action_items(
        meeting_id=meeting_id,
        confirm_ids=body.confirm,
        reject_ids=body.reject,
    )

    if confirmed or rejected:
        try:
            resync_action_items_artifacts(
                session=session, config=config, meeting_id=meeting_id,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠ resync after bulk review failed: {exc}")

    items = (
        session.query(ActionItemORM)
        .filter(ActionItemORM.meeting_id == meeting_id)
        .all()
    )
    response_items = [_to_response(ai, m).model_dump() for ai in items]
    return PaginatedResponse(
        items=response_items,
        total=len(response_items),
        limit=len(response_items),
        offset=0,
    )


@router.post("/confirm-before")
def confirm_proposals_before(
    body: BulkConfirmBeforeRequest,
    storage: Annotated[StorageEngine, Depends(get_storage)],
    session: Annotated[Session, Depends(get_db_session)],
    config: Annotated[AppConfig, Depends(get_config)],
):
    """Bulk-confirm every still-proposed action item from meetings on or
    before ``before_date``.

    One-time admin sweep — exposed on the global ``/actions`` page so the
    user can clear the historical review backlog the proposal-state
    migration produced. Fires the per-meeting markdown / Obsidian re-render
    for each affected meeting.
    """
    raw = (body.before_date or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="before_date is required")
    try:
        # Accept date-only or full ISO datetime; fall back to end-of-day for
        # date-only so 'before_date=2024-01-01' includes meetings on that day.
        if "T" in raw:
            before_dt = datetime.fromisoformat(raw)
        else:
            before_dt = datetime.fromisoformat(f"{raw}T23:59:59")
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid before_date '{raw}'. Use ISO format (YYYY-MM-DD).",
        )

    updated, affected = storage.confirm_proposed_before(before_dt)

    for meeting_id in affected:
        try:
            resync_action_items_artifacts(
                session=session, config=config, meeting_id=meeting_id,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠ resync after confirm-before failed for {meeting_id}: {exc}")

    return {
        "updated": updated,
        "affected_meeting_count": len(affected),
        "before_date": raw,
    }
