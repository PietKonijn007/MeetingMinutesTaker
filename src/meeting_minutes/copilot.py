"""1:1 Copilot — person-centric meeting intelligence for leaders.

Aggregates commitment status, sentiment trajectory, recurring unresolved
topics, inbound commitments, talk patterns, and generates talking points
for an upcoming 1:1 with a specific person.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

# SQLite stores naive datetimes; use naive UTC for all Python-side comparisons.
_utcnow = lambda: datetime.utcnow()  # noqa: E731


def _as_naive(dt: datetime | None) -> datetime | None:
    """Strip tzinfo so comparisons with naive SQLite datetimes don't raise."""
    if dt is None:
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo else dt
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from meeting_minutes.analytics import compute_talk_time_analytics
from meeting_minutes.stats_analytics import (
    SENTIMENT_SCORES,
    _filter_meetings,
    _parse_due,
    commitments_per_person,
    sentiment_trend,
    unresolved_topics,
)
from meeting_minutes.system3.db import (
    ActionItemORM,
    DecisionORM,
    MeetingORM,
    MeetingSeriesMemberORM,
    MeetingSeriesORM,
    PersonORM,
    meeting_attendees,
)

_LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Output models
# ---------------------------------------------------------------------------


class CommitmentItem(BaseModel):
    action_item_id: str
    description: str
    status: str
    due_date: str | None = None
    overdue: bool = False
    source_meeting_id: str
    source_meeting_title: str | None = None
    source_meeting_date: str | None = None


class CommitmentStatus(BaseModel):
    open_items: list[CommitmentItem] = []
    overdue_count: int = 0
    completion_rate: float = 0.0
    total_assigned: int = 0
    total_completed: int = 0
    trend: str | None = None  # "improving", "declining", "stable"


class SentimentPoint(BaseModel):
    meeting_id: str
    title: str | None = None
    date: str | None = None
    sentiment: str
    score: float


class SentimentTrajectory(BaseModel):
    points: list[SentimentPoint] = []
    shift_detected: bool = False
    shift_description: str | None = None
    average_score: float = 0.0


class UnresolvedTopic(BaseModel):
    topic: str
    mention_count: int
    first_mentioned: str | None = None
    last_mentioned: str | None = None
    meeting_ids: list[str] = []


class InboundCommitment(BaseModel):
    action_item_id: str
    description: str
    owner: str | None = None
    due_date: str | None = None
    overdue: bool = False
    source_meeting_id: str
    source_meeting_title: str | None = None
    direction: str  # "you_owe_them" | "others_owe_them"


class TalkPattern(BaseModel):
    meeting_id: str
    meeting_title: str | None = None
    date: str | None = None
    your_pct: float
    their_pct: float


class TalkPatterns(BaseModel):
    meetings: list[TalkPattern] = []
    avg_your_pct: float = 0.0
    avg_their_pct: float = 0.0
    imbalanced: bool = False


class RelationshipContext(BaseModel):
    total_meetings_together: int = 0
    first_meeting_date: str | None = None
    meeting_frequency: str | None = None  # "weekly", "biweekly", etc.
    common_meeting_types: list[str] = []
    last_one_on_one_date: str | None = None
    series_cadence: str | None = None


class CopilotDossier(BaseModel):
    person_id: str
    person_name: str
    generated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    commitment_status: CommitmentStatus = CommitmentStatus()
    sentiment_trajectory: SentimentTrajectory = SentimentTrajectory()
    recurring_unresolved: list[UnresolvedTopic] = []
    inbound_commitments: list[InboundCommitment] = []
    talk_patterns: TalkPatterns = TalkPatterns()
    relationship_context: RelationshipContext = RelationshipContext()
    talking_points: list[str] = []


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class CopilotConfig(BaseModel):
    your_name: str = ""
    lookback_days: int = 90
    sentiment_shift_threshold: int = 2
    talk_ratio_alert: float = 0.65
    llm_talking_points: bool = False


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class CopilotEngine:
    """Generates a 1:1 prep dossier for a person."""

    def __init__(
        self,
        session: Session,
        data_dir: Path,
        config: CopilotConfig | None = None,
    ) -> None:
        self._session = session
        self._data_dir = data_dir
        self._config = config or CopilotConfig()

    def generate(self, person_name_or_id: str, focus: list[str] | None = None) -> CopilotDossier:
        person = self._resolve_person(person_name_or_id)
        if not person:
            return CopilotDossier(
                person_id="unknown",
                person_name=person_name_or_id,
                talking_points=[f"Person '{person_name_or_id}' not found in the database."],
            )

        dossier = CopilotDossier(
            person_id=person.person_id,
            person_name=person.name,
        )

        meetings_with_person = self._meetings_with_person(person)

        dossier.commitment_status = self._commitment_status(person)
        dossier.sentiment_trajectory = self._sentiment_trajectory(person)
        dossier.recurring_unresolved = self._recurring_unresolved(person, meetings_with_person)
        dossier.inbound_commitments = self._inbound_commitments(person, meetings_with_person)
        dossier.talk_patterns = self._talk_patterns(person, meetings_with_person)
        dossier.relationship_context = self._relationship_context(person, meetings_with_person)
        dossier.talking_points = self._generate_talking_points(dossier)

        return dossier

    # ------------------------------------------------------------------
    # Resolve person
    # ------------------------------------------------------------------

    def _resolve_person(self, name_or_id: str) -> PersonORM | None:
        person = self._session.get(PersonORM, name_or_id)
        if person:
            return person
        person = (
            self._session.query(PersonORM)
            .filter(PersonORM.name.ilike(f"%{name_or_id}%"))
            .first()
        )
        return person

    # ------------------------------------------------------------------
    # Meetings with person
    # ------------------------------------------------------------------

    def _meetings_with_person(self, person: PersonORM) -> list[MeetingORM]:
        cutoff = _utcnow() - timedelta(days=self._config.lookback_days)
        meetings = (
            self._session.query(MeetingORM)
            .join(meeting_attendees, MeetingORM.meeting_id == meeting_attendees.c.meeting_id)
            .filter(meeting_attendees.c.person_id == person.person_id)
            .filter(MeetingORM.date >= cutoff)
            .order_by(MeetingORM.date.desc())
            .all()
        )
        return meetings

    # ------------------------------------------------------------------
    # 1. Commitment status
    # ------------------------------------------------------------------

    def _commitment_status(self, person: PersonORM) -> CommitmentStatus:
        now = _utcnow()
        cutoff = now - timedelta(days=self._config.lookback_days)

        all_actions = (
            self._session.query(ActionItemORM)
            .join(MeetingORM, ActionItemORM.meeting_id == MeetingORM.meeting_id)
            .filter(ActionItemORM.owner == person.name)
            .filter(ActionItemORM.proposal_state == "confirmed")
            .filter(MeetingORM.date >= cutoff)
            .all()
        )

        total_assigned = len(all_actions)
        total_completed = sum(1 for a in all_actions if a.status == "done")
        rate = total_completed / total_assigned if total_assigned else 0.0

        open_items = []
        overdue_count = 0
        for ai in all_actions:
            if ai.status in ("done", "cancelled"):
                continue
            due_dt = _as_naive(_parse_due(ai.due_date))
            is_overdue = due_dt is not None and due_dt < now
            if is_overdue:
                overdue_count += 1

            meeting = self._session.get(MeetingORM, ai.meeting_id)
            open_items.append(CommitmentItem(
                action_item_id=ai.action_item_id,
                description=ai.description,
                status=ai.status or "open",
                due_date=ai.due_date,
                overdue=is_overdue,
                source_meeting_id=ai.meeting_id,
                source_meeting_title=meeting.title if meeting else None,
                source_meeting_date=meeting.date.strftime("%Y-%m-%d") if meeting and meeting.date else None,
            ))

        open_items.sort(key=lambda x: (not x.overdue, x.due_date or "9999"))

        # Trend: compare last 30d rate vs previous 60d rate
        trend = None
        mid_cutoff = now - timedelta(days=30)
        recent = [a for a in all_actions if self._action_meeting_date(a) and self._action_meeting_date(a) >= mid_cutoff]
        older = [a for a in all_actions if self._action_meeting_date(a) and self._action_meeting_date(a) < mid_cutoff]
        if len(recent) >= 3 and len(older) >= 3:
            recent_rate = sum(1 for a in recent if a.status == "done") / len(recent)
            older_rate = sum(1 for a in older if a.status == "done") / len(older)
            if recent_rate > older_rate + 0.1:
                trend = "improving"
            elif recent_rate < older_rate - 0.1:
                trend = "declining"
            else:
                trend = "stable"

        return CommitmentStatus(
            open_items=open_items,
            overdue_count=overdue_count,
            completion_rate=round(rate, 3),
            total_assigned=total_assigned,
            total_completed=total_completed,
            trend=trend,
        )

    def _action_meeting_date(self, ai: ActionItemORM) -> datetime | None:
        meeting = self._session.get(MeetingORM, ai.meeting_id)
        return meeting.date if meeting else None

    # ------------------------------------------------------------------
    # 2. Sentiment trajectory
    # ------------------------------------------------------------------

    def _sentiment_trajectory(self, person: PersonORM) -> SentimentTrajectory:
        data = sentiment_trend(
            self._session,
            days=self._config.lookback_days,
            person=person.person_id,
        )

        points = []
        for item in data.get("series", []):
            points.append(SentimentPoint(
                meeting_id=item["meeting_id"],
                title=item.get("title"),
                date=item.get("date"),
                sentiment=item["sentiment"],
                score=item["sentiment_score"],
            ))

        if not points:
            return SentimentTrajectory()

        avg_score = sum(p.score for p in points) / len(points)

        # Detect shift: last N meetings trending lower
        shift_detected = False
        shift_description = None
        threshold = self._config.sentiment_shift_threshold
        if len(points) >= threshold + 2:
            recent = points[-threshold:]
            recent_avg = sum(p.score for p in recent) / len(recent)
            if recent_avg < avg_score - 0.15:
                shift_detected = True
                sentiments = ", ".join(p.sentiment for p in recent)
                shift_description = f"Last {threshold} meetings trending lower ({sentiments}) vs average"

        return SentimentTrajectory(
            points=points,
            shift_detected=shift_detected,
            shift_description=shift_description,
            average_score=round(avg_score, 2),
        )

    # ------------------------------------------------------------------
    # 3. Recurring unresolved topics
    # ------------------------------------------------------------------

    def _recurring_unresolved(self, person: PersonORM, meetings: list[MeetingORM]) -> list[UnresolvedTopic]:
        person_meeting_ids = {m.meeting_id for m in meetings}
        if not person_meeting_ids:
            return []

        data = unresolved_topics(self._session, days=self._config.lookback_days, min_count=2)
        clusters = data.get("clusters", [])

        results = []
        for cluster in clusters:
            cluster_meeting_ids = set(cluster.get("meeting_ids", []))
            overlap = cluster_meeting_ids & person_meeting_ids
            if overlap:
                results.append(UnresolvedTopic(
                    topic=cluster.get("topic_summary", "Unknown topic"),
                    mention_count=cluster.get("meeting_count", len(overlap)),
                    first_mentioned=cluster.get("first_mentioned"),
                    last_mentioned=cluster.get("last_mentioned"),
                    meeting_ids=list(overlap),
                ))

        results.sort(key=lambda t: -t.mention_count)
        return results[:5]

    # ------------------------------------------------------------------
    # 4. Inbound commitments
    # ------------------------------------------------------------------

    def _inbound_commitments(self, person: PersonORM, meetings: list[MeetingORM]) -> list[InboundCommitment]:
        now = _utcnow()
        person_meeting_ids = {m.meeting_id for m in meetings}
        if not person_meeting_ids:
            return []

        your_name = self._config.your_name.strip().lower()

        # Get open actions from meetings this person attended, not owned by them
        actions = (
            self._session.query(ActionItemORM)
            .filter(ActionItemORM.meeting_id.in_(person_meeting_ids))
            .filter(ActionItemORM.owner != person.name)
            .filter(ActionItemORM.status.in_(["open", "in_progress"]))
            .filter(ActionItemORM.proposal_state == "confirmed")
            .all()
        )

        results = []
        for ai in actions:
            owner_lower = (ai.owner or "").lower()
            if your_name and owner_lower == your_name:
                direction = "you_owe_them"
            else:
                direction = "others_owe_them"

            due_dt = _as_naive(_parse_due(ai.due_date))
            is_overdue = due_dt is not None and due_dt < now

            meeting = self._session.get(MeetingORM, ai.meeting_id)
            results.append(InboundCommitment(
                action_item_id=ai.action_item_id,
                description=ai.description,
                owner=ai.owner,
                due_date=ai.due_date,
                overdue=is_overdue,
                source_meeting_id=ai.meeting_id,
                source_meeting_title=meeting.title if meeting else None,
                direction=direction,
            ))

        # Sort: your commitments first, then by overdue
        results.sort(key=lambda x: (x.direction != "you_owe_them", not x.overdue, x.due_date or "9999"))
        return results[:10]

    # ------------------------------------------------------------------
    # 5. Talk patterns
    # ------------------------------------------------------------------

    def _talk_patterns(self, person: PersonORM, meetings: list[MeetingORM]) -> TalkPatterns:
        your_name = self._config.your_name.strip()
        if not your_name:
            return TalkPatterns()

        # Only look at 1:1-type meetings with this person
        one_on_one_types = {"one_on_one", "one_on_one_direct_report", "one_on_one_leader", "one_on_one_peer"}
        one_on_ones = [m for m in meetings if m.meeting_type in one_on_one_types][:5]

        patterns = []
        for meeting in one_on_ones:
            transcript_path = self._data_dir / "transcripts" / f"{meeting.meeting_id}.json"
            analytics = compute_talk_time_analytics(transcript_path)
            if not analytics or not analytics.has_diarization:
                continue

            your_pct = 0.0
            their_pct = 0.0
            for speaker in analytics.speakers:
                if speaker.speaker.lower() == your_name.lower():
                    your_pct = speaker.talk_time_percentage
                elif speaker.speaker.lower() == person.name.lower():
                    their_pct = speaker.talk_time_percentage

            if your_pct == 0.0 and their_pct == 0.0:
                continue

            patterns.append(TalkPattern(
                meeting_id=meeting.meeting_id,
                meeting_title=meeting.title,
                date=meeting.date.strftime("%Y-%m-%d") if meeting.date else None,
                your_pct=round(your_pct, 1),
                their_pct=round(their_pct, 1),
            ))

        if not patterns:
            return TalkPatterns()

        avg_your = sum(p.your_pct for p in patterns) / len(patterns)
        avg_their = sum(p.their_pct for p in patterns) / len(patterns)
        imbalanced = avg_your > self._config.talk_ratio_alert * 100

        return TalkPatterns(
            meetings=patterns,
            avg_your_pct=round(avg_your, 1),
            avg_their_pct=round(avg_their, 1),
            imbalanced=imbalanced,
        )

    # ------------------------------------------------------------------
    # 6. Relationship context
    # ------------------------------------------------------------------

    def _relationship_context(self, person: PersonORM, meetings: list[MeetingORM]) -> RelationshipContext:
        # Total meetings (all time, not just lookback)
        total = (
            self._session.query(MeetingORM)
            .join(meeting_attendees, MeetingORM.meeting_id == meeting_attendees.c.meeting_id)
            .filter(meeting_attendees.c.person_id == person.person_id)
            .count()
        )

        first_date = None
        if meetings:
            oldest = min((m for m in meetings if m.date), key=lambda m: m.date, default=None)
            if oldest:
                first_date = oldest.date.strftime("%Y-%m-%d")

        # Common meeting types
        type_counts: dict[str, int] = {}
        for m in meetings:
            t = m.meeting_type or "other"
            type_counts[t] = type_counts.get(t, 0) + 1
        common_types = sorted(type_counts.keys(), key=lambda t: -type_counts[t])[:3]

        # Last 1:1
        one_on_one_types = {"one_on_one", "one_on_one_direct_report", "one_on_one_leader", "one_on_one_peer"}
        last_1on1 = next(
            (m for m in meetings if m.meeting_type in one_on_one_types),
            None,
        )
        last_1on1_date = last_1on1.date.strftime("%Y-%m-%d") if last_1on1 and last_1on1.date else None

        # Series cadence (if they share a 1:1 series)
        series_cadence = None
        series_rows = (
            self._session.query(MeetingSeriesORM)
            .join(MeetingSeriesMemberORM)
            .filter(MeetingSeriesORM.meeting_type.in_(one_on_one_types))
            .all()
        )
        for series in series_rows:
            member_meeting_ids = {m.meeting_id for m in series.members}
            person_meeting_ids = {m.meeting_id for m in meetings}
            if member_meeting_ids & person_meeting_ids:
                series_cadence = series.cadence
                break

        return RelationshipContext(
            total_meetings_together=total,
            first_meeting_date=first_date,
            meeting_frequency=series_cadence,
            common_meeting_types=common_types,
            last_one_on_one_date=last_1on1_date,
            series_cadence=series_cadence,
        )

    # ------------------------------------------------------------------
    # 7. Talking points (rule-based)
    # ------------------------------------------------------------------

    def _generate_talking_points(self, dossier: CopilotDossier) -> list[str]:
        points = []

        # Overdue items
        if dossier.commitment_status.overdue_count > 0:
            overdue = next((i for i in dossier.commitment_status.open_items if i.overdue), None)
            if overdue:
                points.append(
                    f"Check on overdue item: \"{overdue.description}\" — any blockers?"
                )

        # Sentiment shift
        if dossier.sentiment_trajectory.shift_detected:
            points.append(
                "Sentiment has dipped recently — open-ended check-in: anything on their mind?"
            )

        # Recurring unresolved
        if dossier.recurring_unresolved:
            top = dossier.recurring_unresolved[0]
            points.append(
                f"\"{top.topic}\" has come up {top.mention_count} times without resolution — time to decide?"
            )

        # You owe them something
        you_owe = [c for c in dossier.inbound_commitments if c.direction == "you_owe_them"]
        if you_owe:
            item = you_owe[0]
            points.append(
                f"Update on your commitment: \"{item.description}\""
            )

        # Talk ratio imbalance
        if dossier.talk_patterns.imbalanced:
            points.append(
                f"You spoke {dossier.talk_patterns.avg_your_pct:.0f}% in recent 1:1s — "
                f"try more open questions to create space."
            )

        # Declining completion rate
        if dossier.commitment_status.trend == "declining":
            points.append(
                "Completion rate is declining — discuss workload or priorities?"
            )

        # If nothing flagged, add a generic opener
        if not points:
            points.append("No flags detected — great time for forward-looking career/growth discussion.")

        return points


# ---------------------------------------------------------------------------
# Markdown formatter
# ---------------------------------------------------------------------------


def format_dossier_markdown(dossier: CopilotDossier) -> str:
    """Render a CopilotDossier as readable markdown."""
    lines = [f"# 1:1 Prep: {dossier.person_name}\n"]

    # Relationship context
    ctx = dossier.relationship_context
    if ctx.last_one_on_one_date:
        lines.append(f"Last 1:1: {ctx.last_one_on_one_date}")
    if ctx.series_cadence:
        lines.append(f"Cadence: {ctx.series_cadence}")
    if ctx.total_meetings_together:
        lines.append(f"Total meetings together: {ctx.total_meetings_together}")
    lines.append("")

    # Commitments
    cs = dossier.commitment_status
    lines.append("## Commitments Status")
    lines.append(f"- {len(cs.open_items)} open actions ({cs.overdue_count} overdue)")
    lines.append(f"- Completion rate ({dossier.relationship_context.meeting_frequency or '90 days'}): "
                 f"{cs.completion_rate:.0%} ({cs.total_completed}/{cs.total_assigned})")
    if cs.trend:
        lines.append(f"- Trend: {cs.trend}")
    lines.append("")
    for item in cs.open_items[:5]:
        overdue_tag = " **OVERDUE**" if item.overdue else ""
        due_tag = f" (due: {item.due_date})" if item.due_date else ""
        source = f" ← {item.source_meeting_title}" if item.source_meeting_title else ""
        lines.append(f"  - [{item.status}] {item.description}{due_tag}{overdue_tag}{source}")
    lines.append("")

    # Sentiment
    st = dossier.sentiment_trajectory
    if st.points:
        lines.append("## Sentiment Trajectory")
        recent_labels = " → ".join(p.sentiment for p in st.points[-6:])
        lines.append(f"Recent: {recent_labels}")
        if st.shift_detected:
            lines.append(f"⚠️  {st.shift_description}")
        lines.append("")

    # Recurring unresolved
    if dossier.recurring_unresolved:
        lines.append("## Recurring Unresolved Topics")
        for topic in dossier.recurring_unresolved:
            lines.append(f"- \"{topic.topic}\" — raised {topic.mention_count} times")
        lines.append("")

    # Inbound commitments
    if dossier.inbound_commitments:
        lines.append("## Commitments Involving Them")
        you_owe = [c for c in dossier.inbound_commitments if c.direction == "you_owe_them"]
        others_owe = [c for c in dossier.inbound_commitments if c.direction == "others_owe_them"]
        if you_owe:
            lines.append("**You owe them:**")
            for c in you_owe[:3]:
                overdue_tag = " **OVERDUE**" if c.overdue else ""
                lines.append(f"  - {c.description}{overdue_tag}")
        if others_owe:
            lines.append("**Others owe them:**")
            for c in others_owe[:3]:
                lines.append(f"  - {c.description} (@{c.owner})")
        lines.append("")

    # Talk patterns
    tp = dossier.talk_patterns
    if tp.meetings:
        lines.append("## Talk Patterns (1:1s)")
        lines.append(f"Average: You {tp.avg_your_pct:.0f}% / Them {tp.avg_their_pct:.0f}%")
        if tp.imbalanced:
            lines.append("⚠️  You're speaking more than 65% — create more space for them.")
        lines.append("")

    # Talking points
    lines.append("## Suggested Talking Points")
    for i, point in enumerate(dossier.talking_points, 1):
        lines.append(f"{i}. {point}")
    lines.append("")

    return "\n".join(lines)
