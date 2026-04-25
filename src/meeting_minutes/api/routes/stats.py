"""Statistics endpoints."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from meeting_minutes.api.deps import get_db_session
from meeting_minutes.api.schemas import (
    ActionVelocityWeek,
    PaginatedResponse,
    StatsOverview,
    TypeDistribution,
    WeeklyCount,
)
from meeting_minutes.stats_analytics import (
    commitments_per_person,
    effectiveness_by_type,
    sentiment_trend,
    unresolved_topics,
)
from meeting_minutes.system3.db import ActionItemORM, MeetingORM

router = APIRouter(prefix="/api/stats", tags=["stats"])


def _parse_duration_minutes(duration_str: str | None) -> float | None:
    """Try to extract numeric minutes from a duration string like '15 minutes'."""
    if not duration_str:
        return None
    import re

    match = re.search(r"(\d+)", duration_str)
    if match:
        return float(match.group(1))
    return None


def _week_start(dt: datetime) -> str:
    """Return the ISO date string of the Monday of the week containing dt."""
    monday = dt - timedelta(days=dt.weekday())
    return monday.strftime("%Y-%m-%d")


@router.get("", response_model=StatsOverview)
def get_stats(
    session: Annotated[Session, Depends(get_db_session)],
):
    """Aggregate statistics."""
    total_meetings = session.query(MeetingORM).count()

    # Meetings this week (Monday-Sunday)
    now = datetime.now(timezone.utc)
    week_start = now - timedelta(days=now.weekday())
    week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
    meetings_this_week = (
        session.query(MeetingORM)
        .filter(MeetingORM.date >= week_start)
        .count()
    )

    # Open action items — confirmed-only, since proposals aren't real actions yet
    open_actions = (
        session.query(ActionItemORM)
        .filter(ActionItemORM.status != "done")
        .filter(ActionItemORM.proposal_state == "confirmed")
        .count()
    )

    # Average duration
    all_meetings = session.query(MeetingORM.duration).all()
    durations = [_parse_duration_minutes(m.duration) for m in all_meetings]
    valid_durations = [d for d in durations if d is not None]
    avg_duration = sum(valid_durations) / len(valid_durations) if valid_durations else None

    return StatsOverview(
        total_meetings=total_meetings,
        meetings_this_week=meetings_this_week,
        open_actions=open_actions,
        avg_duration_minutes=round(avg_duration, 1) if avg_duration else None,
    )


@router.get("/meetings-over-time")
def meetings_over_time(
    session: Annotated[Session, Depends(get_db_session)],
):
    """Weekly meeting count for the last 12 weeks."""
    now = datetime.now(timezone.utc)
    twelve_weeks_ago = now - timedelta(weeks=12)

    meetings = (
        session.query(MeetingORM)
        .filter(MeetingORM.date >= twelve_weeks_ago)
        .all()
    )

    # Build week buckets
    buckets: dict[str, int] = {}
    for i in range(12):
        week_dt = now - timedelta(weeks=11 - i)
        key = _week_start(week_dt)
        buckets[key] = 0

    for m in meetings:
        if m.date:
            key = _week_start(m.date)
            if key in buckets:
                buckets[key] += 1

    series = [WeeklyCount(week=k, count=v) for k, v in buckets.items()]
    return {"series": [s.model_dump() for s in series]}


@router.get("/by-type")
def meetings_by_type(
    session: Annotated[Session, Depends(get_db_session)],
):
    """Meeting type distribution."""
    rows = (
        session.query(MeetingORM.meeting_type, func.count(MeetingORM.meeting_id))
        .group_by(MeetingORM.meeting_type)
        .all()
    )

    distribution = [
        TypeDistribution(meeting_type=mt or "other", count=count).model_dump()
        for mt, count in rows
    ]
    return {"distribution": distribution}


@router.get("/action-velocity")
def action_velocity(
    session: Annotated[Session, Depends(get_db_session)],
):
    """Created vs completed action items per week (last 12 weeks)."""
    now = datetime.now(timezone.utc)
    twelve_weeks_ago = now - timedelta(weeks=12)

    # We don't have a created_at on ActionItemORM, so we use the meeting date as proxy
    all_items = (
        session.query(ActionItemORM)
        .join(MeetingORM, ActionItemORM.meeting_id == MeetingORM.meeting_id)
        .filter(MeetingORM.date >= twelve_weeks_ago)
        .filter(ActionItemORM.proposal_state == "confirmed")
        .all()
    )

    # Build week buckets
    created_buckets: dict[str, int] = {}
    completed_buckets: dict[str, int] = {}
    for i in range(12):
        week_dt = now - timedelta(weeks=11 - i)
        key = _week_start(week_dt)
        created_buckets[key] = 0
        completed_buckets[key] = 0

    for ai in all_items:
        meeting = session.get(MeetingORM, ai.meeting_id)
        if meeting and meeting.date:
            key = _week_start(meeting.date)
            if key in created_buckets:
                created_buckets[key] += 1
            if ai.status == "done" and key in completed_buckets:
                completed_buckets[key] += 1

    series = [
        ActionVelocityWeek(
            week=week,
            created=created_buckets[week],
            completed=completed_buckets[week],
        ).model_dump()
        for week in created_buckets
    ]
    return {"series": series}


# ---------------------------------------------------------------------------
# ANA-1 analytics panels
# ---------------------------------------------------------------------------


@router.get("/commitments")
def stats_commitments(
    session: Annotated[Session, Depends(get_db_session)],
    days: int = 90,
    meeting_type: str | None = None,
    series: str | None = None,
):
    """Panel 1 — commitment completion rate per person."""
    return commitments_per_person(
        session, days=days, meeting_type=meeting_type, series_id=series
    )


@router.get("/sentiment")
def stats_sentiment(
    session: Annotated[Session, Depends(get_db_session)],
    days: int = 90,
    person: str | None = None,
    meeting_type: str | None = None,
    series: str | None = None,
):
    """Panel 3 — sentiment trend per person or meeting type."""
    return sentiment_trend(
        session,
        days=days,
        person=person,
        meeting_type=meeting_type,
        series_id=series,
    )


@router.get("/effectiveness")
def stats_effectiveness(
    session: Annotated[Session, Depends(get_db_session)],
    days: int | None = None,
    meeting_type: str | None = None,
    series: str | None = None,
):
    """Panel 4 — % of meetings per type with each effectiveness attribute."""
    return effectiveness_by_type(
        session, days=days, meeting_type=meeting_type, series_id=series
    )


@router.get("/unresolved-topics")
def stats_unresolved_topics(
    session: Annotated[Session, Depends(get_db_session)],
    days: int | None = None,
    min_count: int = 3,
    series: str | None = None,
):
    """Panel 2 — recurring unresolved topics (embedding clusters)."""
    return unresolved_topics(
        session, days=days, min_count=min_count, series_id=series
    )
