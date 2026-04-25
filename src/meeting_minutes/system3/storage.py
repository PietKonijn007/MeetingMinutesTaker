"""Storage engine — CRUD operations on SQLite database."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from meeting_minutes.models import (
    ActionItem,
    ActionItemProposalState,
    ActionItemStatus,
    MinutesData,
)
from meeting_minutes.system3.db import (
    ActionItemORM,
    DecisionORM,
    MeetingORM,
    MinutesORM,
    PersonORM,
    TranscriptORM,
)


class MeetingFilters:
    def __init__(
        self,
        meeting_type: str | None = None,
        after_date: datetime | None = None,
        before_date: datetime | None = None,
        attendee: str | None = None,
    ) -> None:
        self.meeting_type = meeting_type
        self.after_date = after_date
        self.before_date = before_date
        self.attendee = attendee


class ActionItemFilters:
    def __init__(
        self,
        owner: str | None = None,
        status: str | None = None,
        overdue: bool = False,
        proposal_state: str | None = None,
    ) -> None:
        self.owner = owner
        self.status = status
        self.overdue = overdue
        self.proposal_state = proposal_state


class StorageEngine:
    """CRUD operations on SQLite database."""

    def __init__(self, db_session: Session) -> None:
        self._session = db_session

    def upsert_meeting(self, minutes_data: MinutesData) -> MeetingORM:
        """Insert or update meeting record. Returns ORM object."""
        mj = minutes_data.minutes_json
        now = datetime.now(timezone.utc)

        # Parse date
        try:
            meeting_date = datetime.fromisoformat(mj.metadata.date)
        except Exception:
            meeting_date = mj.generated_at

        # Upsert MeetingORM
        meeting = self._session.get(MeetingORM, mj.meeting_id)
        if meeting is None:
            meeting = MeetingORM(
                meeting_id=mj.meeting_id,
                created_at=now,
            )
            self._session.add(meeting)

        meeting.title = mj.metadata.title
        meeting.date = meeting_date
        meeting.duration = mj.metadata.duration
        meeting.meeting_type = mj.meeting_type
        meeting.organizer = mj.metadata.organizer
        meeting.updated_at = now

        # Upsert MinutesORM
        minutes_orm = self._session.get(MinutesORM, mj.meeting_id)
        if minutes_orm is None:
            minutes_orm = MinutesORM(meeting_id=mj.meeting_id)
            self._session.add(minutes_orm)

        minutes_orm.minutes_id = mj.minutes_id
        minutes_orm.markdown_content = mj.minutes_markdown
        minutes_orm.summary = mj.summary
        minutes_orm.generated_at = mj.generated_at
        minutes_orm.llm_model = mj.llm.model
        minutes_orm.sentiment = getattr(mj, "sentiment", None)
        # Store structured data as JSON string if available
        structured = getattr(mj, "structured_data", None)
        if structured is not None:
            import json
            minutes_orm.structured_json = json.dumps(structured)
        else:
            minutes_orm.structured_json = None

        # Upsert TranscriptORM
        if minutes_data.transcript_json:
            tj = minutes_data.transcript_json
            transcript_orm = self._session.get(TranscriptORM, mj.meeting_id)
            if transcript_orm is None:
                transcript_orm = TranscriptORM(meeting_id=mj.meeting_id)
                self._session.add(transcript_orm)

            transcript_orm.full_text = tj.transcript.get("full_text", "")
            transcript_orm.language = tj.metadata.language
            transcript_orm.audio_file_path = tj.metadata.audio_file

        # Delete old action items and re-insert. Regeneration always
        # produces fresh proposals — the user re-reviews after a regen.
        self._session.query(ActionItemORM).filter_by(meeting_id=mj.meeting_id).delete()
        for ai in mj.action_items:
            ps = getattr(ai, "proposal_state", None)
            ps_value = ps.value if hasattr(ps, "value") else (ps or ActionItemProposalState.PROPOSED.value)
            self._session.add(
                ActionItemORM(
                    action_item_id=ai.id,
                    meeting_id=mj.meeting_id,
                    description=ai.description,
                    owner=ai.owner,
                    due_date=ai.due_date,
                    status=ai.status.value,
                    mentioned_at_seconds=ai.mentioned_at_seconds,
                    priority=getattr(ai, "priority", None),
                    proposal_state=ps_value,
                )
            )

        # Delete old decisions and re-insert
        self._session.query(DecisionORM).filter_by(meeting_id=mj.meeting_id).delete()
        for dec in mj.decisions:
            self._session.add(
                DecisionORM(
                    decision_id=dec.id,
                    meeting_id=mj.meeting_id,
                    description=dec.description,
                    made_by=dec.made_by,
                    mentioned_at_seconds=dec.mentioned_at_seconds,
                    rationale=getattr(dec, "rationale", None),
                    confidence=getattr(dec, "confidence", None),
                )
            )

        # Upsert attendees (person entities)
        meeting.attendees = []
        for attendee_name in mj.metadata.attendees:
            person = self.upsert_person(name=attendee_name)
            meeting.attendees.append(person)

        self._session.flush()

        # Update FTS index
        self._upsert_fts(
            meeting_id=mj.meeting_id,
            title=mj.metadata.title,
            transcript_text=(
                minutes_data.transcript_json.transcript.get("full_text", "")
                if minutes_data.transcript_json
                else ""
            ),
            minutes_text=mj.minutes_markdown,
        )

        self._session.commit()
        return meeting

    def _upsert_fts(
        self,
        meeting_id: str,
        title: str,
        transcript_text: str,
        minutes_text: str,
    ) -> None:
        """Insert or replace FTS5 record."""
        # Delete existing entry
        self._session.execute(
            text("DELETE FROM meetings_fts WHERE meeting_id = :mid"),
            {"mid": meeting_id},
        )
        # Insert new entry
        self._session.execute(
            text(
                "INSERT INTO meetings_fts(meeting_id, title, transcript_text, minutes_text) "
                "VALUES (:mid, :title, :tt, :mt)"
            ),
            {
                "mid": meeting_id,
                "title": title or "",
                "tt": transcript_text or "",
                "mt": minutes_text or "",
            },
        )

    def get_meeting(self, meeting_id: str) -> MeetingORM | None:
        return self._session.get(MeetingORM, meeting_id)

    def list_meetings(
        self,
        limit: int = 20,
        offset: int = 0,
        filters: MeetingFilters | None = None,
    ) -> list[MeetingORM]:
        query = self._session.query(MeetingORM)

        if filters:
            if filters.meeting_type:
                query = query.filter(MeetingORM.meeting_type == filters.meeting_type)
            if filters.after_date:
                query = query.filter(MeetingORM.date >= filters.after_date)
            if filters.before_date:
                query = query.filter(MeetingORM.date <= filters.before_date)

        query = query.order_by(MeetingORM.date.desc())
        return query.offset(offset).limit(limit).all()

    def delete_meeting(self, meeting_id: str) -> bool:
        """Delete meeting and all associated data including FTS index."""
        meeting = self._session.get(MeetingORM, meeting_id)
        if meeting is None:
            return False

        self._session.delete(meeting)

        # Remove from FTS index
        self._session.execute(
            text("DELETE FROM meetings_fts WHERE meeting_id = :mid"),
            {"mid": meeting_id},
        )

        self._session.commit()
        return True

    def upsert_person(self, name: str, email: str | None = None) -> PersonORM:
        """Find or create a person record."""
        if email:
            person = (
                self._session.query(PersonORM).filter_by(email=email).first()
            )
            if person:
                person.name = name
                return person

        # Look up by name if no email
        person = self._session.query(PersonORM).filter_by(name=name).first()
        if person:
            if email and not person.email:
                person.email = email
            return person

        # Create new
        person = PersonORM(
            person_id=str(uuid.uuid4()),
            name=name,
            email=email,
        )
        self._session.add(person)
        self._session.flush()
        return person

    def get_action_items(
        self, filters: ActionItemFilters | None = None
    ) -> list[ActionItemORM]:
        query = self._session.query(ActionItemORM)
        if filters:
            if filters.owner:
                query = query.filter(ActionItemORM.owner == filters.owner)
            if filters.status:
                query = query.filter(ActionItemORM.status == filters.status)
            if filters.proposal_state:
                query = query.filter(ActionItemORM.proposal_state == filters.proposal_state)
        return query.all()

    def update_action_item_status(self, action_id: str, status: str) -> bool:
        item = self._session.get(ActionItemORM, action_id)
        if item is None:
            return False
        item.status = status
        self._session.commit()
        return True

    def update_action_item(
        self,
        action_id: str,
        *,
        status: str | None = None,
        proposal_state: str | None = None,
        description: str | None = None,
        owner: str | None = None,
        due_date: str | None = None,
    ) -> ActionItemORM | None:
        """Partial update of an action item. Returns the row, or None if missing.

        Pass ``None`` for fields you don't want to change. ``owner``/``due_date``
        accept empty strings to clear the value (mapped to NULL); use
        ``description="<new>"`` to rename. Caller commits — no implicit
        commit so bulk callers can batch.
        """
        item = self._session.get(ActionItemORM, action_id)
        if item is None:
            return None
        if status is not None:
            item.status = status
        if proposal_state is not None:
            item.proposal_state = proposal_state
        if description is not None:
            item.description = description
        if owner is not None:
            item.owner = owner.strip() or None
        if due_date is not None:
            item.due_date = due_date.strip() or None
        self._session.commit()
        return item

    def bulk_review_action_items(
        self,
        meeting_id: str,
        confirm_ids: list[str],
        reject_ids: list[str],
    ) -> tuple[int, int]:
        """Set proposal_state on a batch of action items in one transaction.

        IDs are intersected with rows belonging to ``meeting_id`` to keep the
        operation scoped — IDs from other meetings are silently ignored.
        Returns ``(confirmed_count, rejected_count)``.
        """
        confirmed = 0
        rejected = 0
        if confirm_ids:
            q = self._session.query(ActionItemORM).filter(
                ActionItemORM.meeting_id == meeting_id,
                ActionItemORM.action_item_id.in_(confirm_ids),
            )
            for item in q.all():
                item.proposal_state = ActionItemProposalState.CONFIRMED.value
                confirmed += 1
        if reject_ids:
            q = self._session.query(ActionItemORM).filter(
                ActionItemORM.meeting_id == meeting_id,
                ActionItemORM.action_item_id.in_(reject_ids),
            )
            for item in q.all():
                item.proposal_state = ActionItemProposalState.REJECTED.value
                rejected += 1
        if confirmed or rejected:
            self._session.commit()
        return confirmed, rejected

    def confirm_proposed_before(self, before_dt: datetime) -> tuple[int, list[str]]:
        """Bulk-confirm every still-proposed action item from meetings on or
        before ``before_dt``.

        Returns ``(updated_count, affected_meeting_ids)``. Used by the
        admin "Confirm all proposals from before date" affordance — the
        one-time backlog clear after the proposal-state migration.
        """
        rows = (
            self._session.query(ActionItemORM, MeetingORM)
            .join(MeetingORM, ActionItemORM.meeting_id == MeetingORM.meeting_id)
            .filter(ActionItemORM.proposal_state == ActionItemProposalState.PROPOSED.value)
            .filter(MeetingORM.date <= before_dt)
            .all()
        )
        affected: set[str] = set()
        updated = 0
        for ai, _m in rows:
            ai.proposal_state = ActionItemProposalState.CONFIRMED.value
            affected.add(ai.meeting_id)
            updated += 1
        if updated:
            self._session.commit()
        return updated, sorted(affected)

    def get_open_action_items_for_attendees(
        self,
        attendee_names: list[str],
        lookback_meetings: int = 5,
        exclude_meeting_id: str | None = None,
    ) -> list[ActionItemORM]:
        """Return open action items from recent meetings that shared attendees.

        Used for prior-action carryover into a new meeting's prompt so the LLM
        can detect and mark acknowledged-closed items. Returns open items from
        up to ``lookback_meetings`` distinct prior meetings where at least one
        attendee overlaps with ``attendee_names``, newest-first.
        """
        if not attendee_names:
            return []

        attendees_lower = {n.strip().lower() for n in attendee_names if n and n.strip()}
        if not attendees_lower:
            return []

        # Find recent meetings whose attendee set overlaps.
        q = (
            self._session.query(MeetingORM)
            .order_by(MeetingORM.date.desc().nullslast())
        )
        if exclude_meeting_id:
            q = q.filter(MeetingORM.meeting_id != exclude_meeting_id)

        matching_meeting_ids: list[str] = []
        for meeting in q.limit(200):
            meeting_attendee_names = {
                (p.name or "").strip().lower() for p in meeting.attendees
            }
            if meeting_attendee_names & attendees_lower:
                matching_meeting_ids.append(meeting.meeting_id)
            if len(matching_meeting_ids) >= lookback_meetings:
                break

        if not matching_meeting_ids:
            return []

        items = (
            self._session.query(ActionItemORM)
            .filter(ActionItemORM.meeting_id.in_(matching_meeting_ids))
            .filter(ActionItemORM.status == ActionItemStatus.OPEN.value)
            # Proposed/rejected items aren't real yet — don't bleed them into
            # the next meeting's prompt.
            .filter(ActionItemORM.proposal_state == ActionItemProposalState.CONFIRMED.value)
            .all()
        )
        return items
