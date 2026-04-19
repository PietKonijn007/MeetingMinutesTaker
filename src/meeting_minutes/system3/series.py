"""Recurring-meeting threading (REC-1).

Groups meetings into a ``meeting_series`` by (exact attendee set,
meeting type) and exposes cross-instance aggregates. Detection is
idempotent — running it repeatedly converges on the same rows.

The detection heuristic intentionally keeps v1 simple:

* Same meeting type.
* Exact attendee-set match across >= 3 meetings.
* Cadence = median inter-meeting interval mapped to
  weekly / biweekly / monthly / irregular buckets.

The spec mentions an 80%-overlap fallback — kept as a documented
follow-up and NOT shipped in v1 so detection stays deterministic.
"""

from __future__ import annotations

import hashlib
import logging
import statistics
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from meeting_minutes.system3.db import (
    ActionItemORM,
    DecisionORM,
    EmbeddingChunkORM,
    MeetingORM,
    MeetingSeriesMemberORM,
    MeetingSeriesORM,
    meeting_attendees,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public data classes
# ---------------------------------------------------------------------------


@dataclass
class SeriesCandidate:
    """A proposed series before it has been written to the DB."""

    attendee_hash: str
    attendee_ids: list[str]
    attendee_names: list[str]
    meeting_type: str
    meeting_ids: list[str]  # chronologically ordered (oldest first)
    cadence: str  # weekly|biweekly|monthly|irregular
    title: str


@dataclass
class SeriesAggregates:
    """Cross-instance summary for a series (used by API + UI)."""

    series_id: str
    open_action_items: list[dict] = field(default_factory=list)
    recent_decisions: list[dict] = field(default_factory=list)
    recurring_topics: list[dict] = field(default_factory=list)


@dataclass
class DetectChangeSummary:
    """Result of ``detect_and_upsert`` — returned by the CLI/API."""

    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def compute_attendee_hash(person_ids: Iterable[str]) -> str:
    """Stable sha256 of sorted attendee person_ids."""
    joined = "|".join(sorted(pid for pid in person_ids if pid))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def classify_cadence(dates: list[datetime]) -> str:
    """Map the median inter-meeting interval to a cadence bucket.

    5-10 days → weekly, 11-18 → biweekly, 24-35 → monthly, else irregular.
    """
    if len(dates) < 2:
        return "irregular"
    sorted_dates = sorted(dates)
    intervals_days = [
        (sorted_dates[i + 1] - sorted_dates[i]).total_seconds() / 86400.0
        for i in range(len(sorted_dates) - 1)
    ]
    median = statistics.median(intervals_days)
    if 5 <= median <= 10:
        return "weekly"
    if 11 <= median <= 18:
        return "biweekly"
    if 24 <= median <= 35:
        return "monthly"
    return "irregular"


def _generate_title(meeting_type: str, attendee_names: list[str], cadence: str) -> str:
    """Auto-generate a human-friendly series title.

    Examples:
    * "1:1 with Jon"
    * "Standup with Jon & Sarah (weekly)"
    * "Planning" (no attendees resolved)
    """
    nice_type = {
        "one_on_one": "1:1",
        "standup": "Standup",
        "team_meeting": "Team meeting",
        "customer_meeting": "Customer meeting",
        "decision_meeting": "Decision meeting",
        "brainstorm": "Brainstorm",
        "retrospective": "Retrospective",
        "planning": "Planning",
        "other": "Meeting",
    }.get(meeting_type, meeting_type.replace("_", " ").title())

    # Show up to three names.
    shown = [n for n in attendee_names if n][:3]
    if shown:
        joined = " & ".join(shown)
        base = f"{nice_type} with {joined}"
    else:
        base = nice_type

    if cadence and cadence != "irregular":
        return f"{base} ({cadence})"
    return base


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def detect_series(session: Session) -> list[SeriesCandidate]:
    """Scan the meetings table and return series candidates.

    Pure read — does not write. Meetings with no attendees or missing
    date are excluded (attendees are required to build a stable hash).
    """
    meetings: list[MeetingORM] = session.query(MeetingORM).all()
    groups: dict[tuple[str, str], list[MeetingORM]] = {}

    for m in meetings:
        if not m.meeting_type or not m.date:
            continue
        attendee_ids = sorted(a.person_id for a in m.attendees if a.person_id)
        if not attendee_ids:
            continue
        key = (compute_attendee_hash(attendee_ids), m.meeting_type)
        groups.setdefault(key, []).append(m)

    candidates: list[SeriesCandidate] = []
    for (attendee_hash, meeting_type), group in groups.items():
        if len(group) < 3:
            continue
        group.sort(key=lambda m: m.date)

        # All members share the same attendee set, so grab names from the
        # first one.
        first = group[0]
        attendee_ids = sorted(a.person_id for a in first.attendees if a.person_id)
        id_to_name = {a.person_id: a.name for a in first.attendees}
        attendee_names = [id_to_name.get(pid, "") for pid in attendee_ids]

        cadence = classify_cadence([m.date for m in group])
        title = _generate_title(meeting_type, attendee_names, cadence)

        candidates.append(
            SeriesCandidate(
                attendee_hash=attendee_hash,
                attendee_ids=attendee_ids,
                attendee_names=attendee_names,
                meeting_type=meeting_type,
                meeting_ids=[m.meeting_id for m in group],
                cadence=cadence,
                title=title,
            )
        )

    return candidates


def upsert_series(session: Session, candidate: SeriesCandidate) -> tuple[MeetingSeriesORM, str]:
    """Insert or update a series row + its members. Returns ``(row, change)``
    where ``change`` is one of ``created``, ``updated``, ``unchanged``.
    """
    now = datetime.now(timezone.utc)
    existing = (
        session.query(MeetingSeriesORM)
        .filter_by(
            attendee_hash=candidate.attendee_hash,
            meeting_type=candidate.meeting_type,
        )
        .one_or_none()
    )

    if existing is None:
        series = MeetingSeriesORM(
            series_id=f"s-{uuid.uuid4().hex[:12]}",
            title=candidate.title,
            meeting_type=candidate.meeting_type,
            cadence=candidate.cadence,
            attendee_hash=candidate.attendee_hash,
            created_at=now,
            last_detected_at=now,
        )
        session.add(series)
        session.flush()
        _sync_members(session, series.series_id, candidate.meeting_ids)
        session.commit()
        return series, "created"

    # Compare against current members + metadata; only mark as updated if
    # anything actually changes so the CLI summary is useful.
    existing_member_ids = {
        m.meeting_id
        for m in session.query(MeetingSeriesMemberORM)
        .filter_by(series_id=existing.series_id)
        .all()
    }
    new_member_ids = set(candidate.meeting_ids)

    metadata_changed = (
        existing.title != candidate.title
        or existing.cadence != candidate.cadence
    )
    membership_changed = existing_member_ids != new_member_ids

    if metadata_changed or membership_changed:
        existing.title = candidate.title
        existing.cadence = candidate.cadence
        existing.last_detected_at = now
        _sync_members(session, existing.series_id, candidate.meeting_ids)
        session.commit()
        return existing, "updated"

    # Touch last_detected_at anyway — useful signal that detection ran.
    existing.last_detected_at = now
    session.commit()
    return existing, "unchanged"


def _sync_members(session: Session, series_id: str, meeting_ids: list[str]) -> None:
    """Replace the member list of ``series_id`` with the given ``meeting_ids``."""
    session.query(MeetingSeriesMemberORM).filter_by(series_id=series_id).delete()
    for mid in meeting_ids:
        session.add(MeetingSeriesMemberORM(series_id=series_id, meeting_id=mid))
    session.flush()


def detect_and_upsert(session: Session) -> DetectChangeSummary:
    """Run detection + upsert in one pass; also prune series whose
    attendee set no longer produces a valid candidate (e.g. a member
    meeting's type was changed).
    """
    summary = DetectChangeSummary()
    candidates = detect_series(session)
    seen_keys: set[tuple[str, str]] = set()
    for cand in candidates:
        seen_keys.add((cand.attendee_hash, cand.meeting_type))
        _, change = upsert_series(session, cand)
        if change == "created":
            summary.created.append(cand.title)
        elif change == "updated":
            summary.updated.append(cand.title)
        else:
            summary.unchanged.append(cand.title)

    # Prune series that are no longer backed by a valid candidate — e.g.
    # someone changed a meeting's type so the group dropped below 3, or
    # an attendee was removed.
    all_series = session.query(MeetingSeriesORM).all()
    for s in all_series:
        if (s.attendee_hash, s.meeting_type) in seen_keys:
            continue
        # Drop the series and its member rows.
        session.query(MeetingSeriesMemberORM).filter_by(series_id=s.series_id).delete()
        session.delete(s)
        summary.updated.append(f"{s.title} (removed)")
    if all_series:
        session.commit()
    return summary


# ---------------------------------------------------------------------------
# Aggregates
# ---------------------------------------------------------------------------


def series_aggregates(session: Session, series_id: str) -> SeriesAggregates:
    """Cross-member aggregates for a series: open actions, recent
    decisions, and topics that recur across >= 2 meetings.
    """
    member_ids = [
        m.meeting_id
        for m in session.query(MeetingSeriesMemberORM).filter_by(series_id=series_id).all()
    ]
    if not member_ids:
        return SeriesAggregates(series_id=series_id)

    # Open action items across all members, oldest meeting first so the
    # "first seen" pattern is clear to the user.
    member_meetings = (
        session.query(MeetingORM)
        .filter(MeetingORM.meeting_id.in_(member_ids))
        .order_by(MeetingORM.date.asc())
        .all()
    )
    meeting_date_by_id = {m.meeting_id: m.date for m in member_meetings}

    action_rows = (
        session.query(ActionItemORM)
        .filter(
            ActionItemORM.meeting_id.in_(member_ids),
            ActionItemORM.status != "done",
        )
        .all()
    )
    # Earliest occurrence per description — crude carry-over detection.
    first_seen: dict[str, tuple[str, datetime]] = {}
    for ai in action_rows:
        key = (ai.description or "").strip().lower()
        if not key:
            continue
        md = meeting_date_by_id.get(ai.meeting_id)
        if key not in first_seen or (md and md < first_seen[key][1]):
            first_seen[key] = (ai.meeting_id, md or datetime.min.replace(tzinfo=timezone.utc))

    open_action_items = [
        {
            "action_item_id": ai.action_item_id,
            "description": ai.description,
            "owner": ai.owner,
            "due_date": ai.due_date,
            "status": ai.status or "open",
            "meeting_id": ai.meeting_id,
            "first_seen_meeting_id": first_seen.get(
                (ai.description or "").strip().lower(), (ai.meeting_id, None)
            )[0],
        }
        for ai in action_rows
    ]

    # Recent decisions across all members, newest first.
    decision_rows = (
        session.query(DecisionORM)
        .filter(DecisionORM.meeting_id.in_(member_ids))
        .all()
    )
    decision_rows.sort(
        key=lambda d: meeting_date_by_id.get(d.meeting_id) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    recent_decisions = [
        {
            "decision_id": d.decision_id,
            "description": d.description,
            "made_by": d.made_by,
            "rationale": d.rationale,
            "meeting_id": d.meeting_id,
            "meeting_date": (
                meeting_date_by_id[d.meeting_id].isoformat()
                if meeting_date_by_id.get(d.meeting_id)
                else None
            ),
        }
        for d in decision_rows[:20]
    ]

    # Recurring topics: pull parking_lot + discussion_point chunks for
    # all members, bucket by normalized text, and keep buckets that
    # touch >= 2 distinct meetings.
    chunks = (
        session.query(EmbeddingChunkORM)
        .filter(
            EmbeddingChunkORM.meeting_id.in_(member_ids),
            EmbeddingChunkORM.chunk_type.in_(("discussion_point", "parking_lot")),
        )
        .all()
    )
    topic_buckets: dict[str, dict] = {}
    for c in chunks:
        text = (c.text or "").strip()
        if not text:
            continue
        # Normalize to first sentence / 80 chars so nearly identical
        # topics group together without pulling in sqlite-vec here. The
        # heavier clustering for ANA-1 Panel 2 lives in analytics.py.
        norm = text.split(".")[0].strip().lower()[:80]
        if not norm:
            continue
        bucket = topic_buckets.setdefault(
            norm,
            {"topic_summary": text[:140], "meeting_ids": set(), "count": 0},
        )
        bucket["meeting_ids"].add(c.meeting_id)
        bucket["count"] += 1

    recurring_topics = [
        {
            "topic_summary": b["topic_summary"],
            "meeting_ids": sorted(b["meeting_ids"]),
            "mention_count": b["count"],
        }
        for b in topic_buckets.values()
        if len(b["meeting_ids"]) >= 2
    ]
    recurring_topics.sort(key=lambda r: -r["mention_count"])

    return SeriesAggregates(
        series_id=series_id,
        open_action_items=open_action_items,
        recent_decisions=recent_decisions,
        recurring_topics=recurring_topics,
    )


# ---------------------------------------------------------------------------
# Lookup helper
# ---------------------------------------------------------------------------


def series_for_meeting(session: Session, meeting_id: str) -> Optional[MeetingSeriesORM]:
    """Return the series a meeting belongs to, if any."""
    member = (
        session.query(MeetingSeriesMemberORM).filter_by(meeting_id=meeting_id).one_or_none()
    )
    if member is None:
        return None
    return session.get(MeetingSeriesORM, member.series_id)
