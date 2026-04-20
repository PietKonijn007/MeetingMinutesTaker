"""Recurring-meeting series endpoints (REC-1)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from meeting_minutes.api.deps import get_db_session
from meeting_minutes.system3.db import (
    MeetingORM,
    MeetingSeriesMemberORM,
    MeetingSeriesORM,
    PersonORM,
    meeting_attendees,
)
from meeting_minutes.system3.series import (
    detect_and_upsert,
    series_aggregates,
    series_for_meeting,
)

router = APIRouter(prefix="/api/series", tags=["series"])


def _series_summary(session: Session, series: MeetingSeriesORM) -> dict:
    """Build the list-row summary for a series."""
    member_ids = [
        m.meeting_id
        for m in session.query(MeetingSeriesMemberORM).filter_by(series_id=series.series_id).all()
    ]
    last_meeting = None
    if member_ids:
        last_meeting = (
            session.query(MeetingORM)
            .filter(MeetingORM.meeting_id.in_(member_ids))
            .order_by(MeetingORM.date.desc())
            .first()
        )

    # Resolve current attendee names from the attendee_hash — we stored
    # the hash not the names. Pull them from the most recent member.
    attendee_names: list[str] = []
    attendee_ids: list[str] = []
    if last_meeting is not None:
        attendee_names = [a.name for a in last_meeting.attendees]
        attendee_ids = [a.person_id for a in last_meeting.attendees]

    return {
        "series_id": series.series_id,
        "title": series.title,
        "meeting_type": series.meeting_type,
        "cadence": series.cadence,
        "member_count": len(member_ids),
        "attendee_names": attendee_names,
        "attendee_ids": attendee_ids,
        "last_meeting_date": last_meeting.date.isoformat() if last_meeting and last_meeting.date else None,
        "created_at": series.created_at.isoformat() if series.created_at else None,
        "last_detected_at": series.last_detected_at.isoformat() if series.last_detected_at else None,
    }


@router.get("")
def list_series(
    session: Annotated[Session, Depends(get_db_session)],
):
    """List all detected series."""
    rows = session.query(MeetingSeriesORM).order_by(MeetingSeriesORM.last_detected_at.desc()).all()
    return {"series": [_series_summary(session, s) for s in rows]}


@router.post("/detect")
def detect_series_endpoint(
    session: Annotated[Session, Depends(get_db_session)],
):
    """Run detection on demand. Returns created/updated/unchanged lists."""
    summary = detect_and_upsert(session)
    return {
        "created": summary.created,
        "updated": summary.updated,
        "unchanged": summary.unchanged,
    }


@router.get("/{series_id}")
def get_series(
    series_id: str,
    session: Annotated[Session, Depends(get_db_session)],
):
    """Full series detail: metadata, members, aggregates."""
    series = session.get(MeetingSeriesORM, series_id)
    if series is None:
        raise HTTPException(status_code=404, detail=f"Series {series_id} not found")

    member_ids = [
        m.meeting_id
        for m in session.query(MeetingSeriesMemberORM).filter_by(series_id=series_id).all()
    ]
    members_qs = (
        session.query(MeetingORM)
        .filter(MeetingORM.meeting_id.in_(member_ids))
        .order_by(MeetingORM.date.asc())
        .all()
    )
    members = [
        {
            "meeting_id": m.meeting_id,
            "title": m.title,
            "date": m.date.isoformat() if m.date else None,
            "duration": m.duration,
            "summary": m.minutes.summary if m.minutes else None,
            "action_item_count": len(m.action_items),
            "decision_count": len(m.decisions),
        }
        for m in members_qs
    ]

    agg = series_aggregates(session, series_id)

    return {
        **_series_summary(session, series),
        "members": members,
        "aggregates": {
            "open_action_items": agg.open_action_items,
            "recent_decisions": agg.recent_decisions,
            "recurring_topics": agg.recurring_topics,
        },
    }


@router.get("/{series_id}/export")
def export_series(
    series_id: str,
    session: Annotated[Session, Depends(get_db_session)],
    format: str = Query("pdf"),
    with_transcript: bool = Query(False),
):
    """Bundle every member meeting in the series into a single ZIP (EXP-1)."""
    from meeting_minutes.export import (
        ExportDependencyMissing,
        export as render_export,
        slugify,
    )
    from meeting_minutes.export.bundle import make_zip

    series = session.get(MeetingSeriesORM, series_id)
    if series is None:
        raise HTTPException(status_code=404, detail=f"Series {series_id} not found")
    if format not in ("pdf", "docx", "md"):
        raise HTTPException(
            status_code=400,
            detail="Unsupported format. Use 'pdf', 'docx' or 'md'.",
        )

    member_ids = [
        m.meeting_id
        for m in session.query(MeetingSeriesMemberORM).filter_by(series_id=series_id).all()
    ]
    members = (
        session.query(MeetingORM)
        .filter(MeetingORM.meeting_id.in_(member_ids))
        .order_by(MeetingORM.date.asc())
        .all()
    )
    if not members:
        raise HTTPException(status_code=400, detail="Series has no meetings to export")

    results = []
    for m in members:
        if m.minutes is None or not (m.minutes.markdown_content or "").strip():
            continue
        try:
            results.append(
                render_export(m, format=format, with_transcript=with_transcript)
            )
        except ExportDependencyMissing as exc:
            raise HTTPException(status_code=501, detail=str(exc))
    if not results:
        raise HTTPException(status_code=400, detail="No member meetings had minutes to export")

    zip_bytes = make_zip(results)
    zip_name = f"{slugify(series.title or series.series_id)}.zip"
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_name}"'},
    )


# ---------------------------------------------------------------------------
# Meeting → series lookup (used by the "Part of series" link in the UI).
# Mounted on the series router to keep everything in one place; FastAPI
# resolves the URL path regardless of the prefix above.
# ---------------------------------------------------------------------------


meeting_lookup_router = APIRouter(prefix="/api/meetings", tags=["series"])


@meeting_lookup_router.get("/{meeting_id}/series")
def meeting_series_lookup(
    meeting_id: str,
    session: Annotated[Session, Depends(get_db_session)],
):
    """Return the series this meeting belongs to, or 404."""
    series = series_for_meeting(session, meeting_id)
    if series is None:
        return {"series": None}
    return {"series": _series_summary(session, series)}
