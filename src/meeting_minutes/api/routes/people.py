"""People endpoints."""

from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from meeting_minutes.api.deps import get_db_session
from meeting_minutes.api.schemas import (
    ActionItemResponse,
    MeetingListItem,
    PaginatedResponse,
    PersonDetailResponse,
    PersonResponse,
)
from meeting_minutes.system3.db import ActionItemORM, MeetingORM, PersonORM, meeting_attendees

router = APIRouter(prefix="/api/people", tags=["people"])


def _person_detail(session: Session, person: PersonORM) -> PersonDetailResponse:
    """Build a PersonDetailResponse with computed stats."""
    # Count meetings
    meeting_count = (
        session.query(meeting_attendees)
        .filter(meeting_attendees.c.person_id == person.person_id)
        .count()
    )

    # Count open action items (match by owner name)
    open_action_count = (
        session.query(ActionItemORM)
        .filter(ActionItemORM.owner == person.name, ActionItemORM.status != "done")
        .count()
    )

    # Last meeting date
    last_meeting = (
        session.query(MeetingORM)
        .join(meeting_attendees, MeetingORM.meeting_id == meeting_attendees.c.meeting_id)
        .filter(meeting_attendees.c.person_id == person.person_id)
        .order_by(MeetingORM.date.desc())
        .first()
    )
    last_date = last_meeting.date.isoformat() if last_meeting and last_meeting.date else None

    return PersonDetailResponse(
        person_id=person.person_id,
        name=person.name,
        email=person.email,
        meeting_count=meeting_count,
        open_action_count=open_action_count,
        last_meeting_date=last_date,
    )


@router.get("", response_model=PaginatedResponse)
def list_people(
    session: Annotated[Session, Depends(get_db_session)],
    q: Optional[str] = Query(None, description="Search by name"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List all known people."""
    query = session.query(PersonORM)
    if q:
        query = query.filter(PersonORM.name.ilike(f"%{q}%"))

    total = query.count()
    people = query.order_by(PersonORM.name).offset(offset).limit(limit).all()

    items = [_person_detail(session, p).model_dump() for p in people]

    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/{person_id}", response_model=PersonDetailResponse)
def get_person(
    person_id: str,
    session: Annotated[Session, Depends(get_db_session)],
):
    """Get person detail."""
    person = session.get(PersonORM, person_id)
    if person is None:
        raise HTTPException(status_code=404, detail=f"Person {person_id} not found")
    return _person_detail(session, person)


@router.get("/{person_id}/meetings", response_model=PaginatedResponse)
def person_meetings(
    person_id: str,
    session: Annotated[Session, Depends(get_db_session)],
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """Get meetings a person attended."""
    person = session.get(PersonORM, person_id)
    if person is None:
        raise HTTPException(status_code=404, detail=f"Person {person_id} not found")

    query = (
        session.query(MeetingORM)
        .join(meeting_attendees, MeetingORM.meeting_id == meeting_attendees.c.meeting_id)
        .filter(meeting_attendees.c.person_id == person_id)
        .order_by(MeetingORM.date.desc())
    )

    total = query.count()
    meetings = query.offset(offset).limit(limit).all()

    items = [
        MeetingListItem(
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
        ).model_dump()
        for m in meetings
    ]

    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/{person_id}/action-items", response_model=PaginatedResponse)
def person_action_items(
    person_id: str,
    session: Annotated[Session, Depends(get_db_session)],
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Get action items assigned to a person."""
    person = session.get(PersonORM, person_id)
    if person is None:
        raise HTTPException(status_code=404, detail=f"Person {person_id} not found")

    query = session.query(ActionItemORM).filter(ActionItemORM.owner == person.name)

    total = query.count()
    items_orm = query.offset(offset).limit(limit).all()

    items = []
    for ai in items_orm:
        meeting_title = None
        if ai.meeting_id:
            m = session.get(MeetingORM, ai.meeting_id)
            if m:
                meeting_title = m.title
        items.append(
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

    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)
