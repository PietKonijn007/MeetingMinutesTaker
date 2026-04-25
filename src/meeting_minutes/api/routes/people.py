"""People endpoints."""

from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from meeting_minutes.api.deps import get_db_session
from meeting_minutes.api.schemas import (
    ActionItemResponse,
    MeetingListItem,
    PaginatedResponse,
    PersonDetailResponse,
    PersonResponse,
)
from meeting_minutes.system3.db import (
    ActionItemORM,
    DecisionORM,
    MeetingORM,
    PersonORM,
    meeting_attendees,
)

router = APIRouter(prefix="/api/people", tags=["people"])


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class PersonUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None


class PersonCreate(BaseModel):
    name: str
    email: Optional[str] = None


class PersonMergeRequest(BaseModel):
    target_id: str  # the person to merge INTO (source = person_id in URL)
    rename_actions: bool = True  # if True, rename owner in action_items and decisions


def _person_detail(session: Session, person: PersonORM) -> PersonDetailResponse:
    """Build a PersonDetailResponse with computed stats."""
    # Count meetings
    meeting_count = (
        session.query(meeting_attendees)
        .filter(meeting_attendees.c.person_id == person.person_id)
        .count()
    )

    # Count open action items (match by owner name) — confirmed-only.
    open_action_count = (
        session.query(ActionItemORM)
        .filter(
            ActionItemORM.owner == person.name,
            ActionItemORM.status != "done",
            ActionItemORM.proposal_state == "confirmed",
        )
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


@router.post("", response_model=PersonDetailResponse, status_code=201)
def create_person(
    body: PersonCreate,
    session: Annotated[Session, Depends(get_db_session)],
):
    """Create a new person. Used by the SPK-1 inline-create flow when the
    user has a long unknown speaker and wants to name them without leaving
    the meeting page.
    """
    import uuid

    name = body.name.strip() if body.name else ""
    email = body.email.strip() if body.email else None
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    if email:
        existing = session.query(PersonORM).filter(PersonORM.email == email).first()
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"A person ({existing.name}) already uses that email",
            )

    person = PersonORM(person_id=f"p-{uuid.uuid4().hex[:8]}", name=name, email=email)
    session.add(person)
    session.commit()
    return _person_detail(session, person)


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


# ---------------------------------------------------------------------------
# Mutations: edit / delete / merge
# ---------------------------------------------------------------------------


@router.patch("/{person_id}", response_model=PersonDetailResponse)
def update_person(
    person_id: str,
    body: PersonUpdate,
    session: Annotated[Session, Depends(get_db_session)],
):
    """Update a person's name and/or email.

    If the name changes, propagate the new name to action_items.owner and
    decisions.made_by where the old name was referenced (so historical
    attributions stay consistent).
    """
    person = session.get(PersonORM, person_id)
    if person is None:
        raise HTTPException(status_code=404, detail=f"Person {person_id} not found")

    old_name = person.name
    new_name = body.name.strip() if body.name else None
    new_email = body.email.strip() if body.email else None

    if new_email is not None:
        # Check uniqueness
        existing = (
            session.query(PersonORM)
            .filter(PersonORM.email == new_email, PersonORM.person_id != person_id)
            .first()
        )
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"Another person ({existing.name}) already uses that email. Use merge instead.",
            )
        person.email = new_email or None

    if new_name and new_name != old_name:
        person.name = new_name
        # Propagate rename to owner fields
        session.query(ActionItemORM).filter(ActionItemORM.owner == old_name).update(
            {ActionItemORM.owner: new_name}, synchronize_session=False
        )
        session.query(DecisionORM).filter(DecisionORM.made_by == old_name).update(
            {DecisionORM.made_by: new_name}, synchronize_session=False
        )

    session.commit()
    return _person_detail(session, person)


@router.delete("/{person_id}")
def delete_person(
    person_id: str,
    session: Annotated[Session, Depends(get_db_session)],
):
    """Delete a person. Removes from meeting_attendees. Does NOT touch
    action_items/decisions — their `owner`/`made_by` strings are kept as
    historical attribution.
    """
    person = session.get(PersonORM, person_id)
    if person is None:
        raise HTTPException(status_code=404, detail=f"Person {person_id} not found")

    # Remove from all meetings' attendee lists
    session.execute(
        meeting_attendees.delete().where(meeting_attendees.c.person_id == person_id)
    )
    session.delete(person)
    session.commit()
    return {"deleted": person_id, "name": person.name}


@router.post("/{person_id}/merge")
def merge_person(
    person_id: str,
    body: PersonMergeRequest,
    session: Annotated[Session, Depends(get_db_session)],
):
    """Merge person (person_id) INTO target (body.target_id).

    - All meeting_attendees rows for person_id get re-pointed to target_id
      (deduplicated: if target was already on the meeting, source is just dropped).
    - If rename_actions=True, owner/made_by references to the source name
      are rewritten to the target's name in action_items and decisions.
    - The source person is deleted. The target keeps its name and email.
    """
    if person_id == body.target_id:
        raise HTTPException(status_code=400, detail="Cannot merge a person into themselves")

    source = session.get(PersonORM, person_id)
    if source is None:
        raise HTTPException(status_code=404, detail=f"Source person {person_id} not found")
    target = session.get(PersonORM, body.target_id)
    if target is None:
        raise HTTPException(status_code=404, detail=f"Target person {body.target_id} not found")

    source_name = source.name
    target_name = target.name

    # Step 1: rewrite meeting_attendees. Delete source rows if target is already
    # on the same meeting; otherwise point them to target.
    # Find meetings where target is already an attendee:
    target_meetings = {
        r[0]
        for r in session.execute(
            meeting_attendees.select().where(meeting_attendees.c.person_id == body.target_id)
        ).fetchall()
        for r in [r]  # row is (meeting_id, person_id) tuple, but we want meeting_id
    }
    # Simpler: just fetch meeting_ids directly
    target_meetings = {
        row[0]
        for row in session.execute(
            meeting_attendees.select().where(meeting_attendees.c.person_id == body.target_id)
        ).fetchall()
    }

    source_rows = session.execute(
        meeting_attendees.select().where(meeting_attendees.c.person_id == person_id)
    ).fetchall()

    for row in source_rows:
        m_id = row[0]
        if m_id in target_meetings:
            # Drop duplicate
            session.execute(
                meeting_attendees.delete().where(
                    (meeting_attendees.c.meeting_id == m_id)
                    & (meeting_attendees.c.person_id == person_id)
                )
            )
        else:
            # Point to target
            session.execute(
                meeting_attendees.update()
                .where(
                    (meeting_attendees.c.meeting_id == m_id)
                    & (meeting_attendees.c.person_id == person_id)
                )
                .values(person_id=body.target_id)
            )
            target_meetings.add(m_id)

    # Step 2: rename owner/made_by references
    renamed_actions = 0
    renamed_decisions = 0
    if body.rename_actions and source_name and source_name != target_name:
        renamed_actions = (
            session.query(ActionItemORM)
            .filter(ActionItemORM.owner == source_name)
            .update({ActionItemORM.owner: target_name}, synchronize_session=False)
        )
        renamed_decisions = (
            session.query(DecisionORM)
            .filter(DecisionORM.made_by == source_name)
            .update({DecisionORM.made_by: target_name}, synchronize_session=False)
        )

    # Step 3: carry over email if source has one and target doesn't
    if source.email and not target.email:
        target.email = source.email

    # Step 4: delete source
    session.delete(source)
    session.commit()

    return {
        "merged": person_id,
        "into": body.target_id,
        "target_name": target_name,
        "renamed_action_items": renamed_actions,
        "renamed_decisions": renamed_decisions,
    }
