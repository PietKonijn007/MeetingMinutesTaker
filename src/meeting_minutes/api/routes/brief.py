"""Pre-meeting briefing endpoint (BRF-1).

Aggregates six data sections for a set of attendees + optional meeting
type and returns them as a single ``BriefingPayload``. All sections are
pure SQL queries over existing tables; no LLM call runs in the default
path so the endpoint is cheap and deterministic.

The optional ``brief.summarize_with_llm`` config toggle can attach a
short LLM-generated synthesis as ``summary`` — off by default.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from meeting_minutes.api.deps import get_config, get_db_session
from meeting_minutes.config import AppConfig
from meeting_minutes.stats_analytics import SENTIMENT_SCORES
from meeting_minutes.system3.db import (
    ActionItemORM,
    DecisionORM,
    EmbeddingChunkORM,
    MeetingORM,
    MinutesORM,
    PersonORM,
    meeting_attendees,
)
from meeting_minutes.system3.series import compute_attendee_hash

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/brief", tags=["brief"])


# ---------------------------------------------------------------------------
# Payload types
# ---------------------------------------------------------------------------


class BriefAttendee(BaseModel):
    person_id: str
    name: str
    email: str | None = None
    # SPK-1 — speaker label to pre-fill in the record panel. Blank when
    # the person doesn't yet have confirmed voice samples.
    last_speaker_label: str | None = None


class BriefSeriesCard(BaseModel):
    series_id: str
    title: str
    meeting_type: str
    cadence: str | None = None
    member_count: int


class BriefWhoAndWhenLast(BaseModel):
    attendees: list[BriefAttendee]
    last_meeting_id: str | None = None
    last_meeting_date: str | None = None
    last_meeting_title: str | None = None
    cadence: str | None = None
    series: BriefSeriesCard | None = None
    total_prior_meetings: int = 0


class BriefOpenCommitment(BaseModel):
    action_id: str
    owner: str | None = None
    description: str
    due_date: str | None = None
    overdue: bool = False
    meeting_id: str | None = None
    meeting_date: str | None = None


class BriefUnresolvedTopic(BaseModel):
    text: str
    first_mentioned: str | None = None
    last_mentioned: str | None = None
    meeting_ids: list[str]


class BriefSentimentPoint(BaseModel):
    date: str | None
    score: float
    sentiment: str
    meeting_id: str


class BriefPersonSentiment(BaseModel):
    person_id: str
    name: str
    scores: list[BriefSentimentPoint]


class BriefRecentDecision(BaseModel):
    decision_id: str
    description: str
    made_by: str | None = None
    date: str | None = None
    rationale: str | None = None
    meeting_id: str | None = None


class BriefContextExcerpt(BaseModel):
    meeting_id: str
    date: str | None = None
    title: str | None = None
    chunk_text: str
    chunk_type: str | None = None
    score: float | None = None


class BriefSuggestedStart(BaseModel):
    title: str
    meeting_type: str
    attendee_labels: list[str]
    carry_forward_note: str


class BriefingPayload(BaseModel):
    people: list[BriefAttendee]
    meeting_type: str | None = None
    who_and_when_last: BriefWhoAndWhenLast
    open_commitments: list[BriefOpenCommitment]
    unresolved_topics: list[BriefUnresolvedTopic]
    recent_sentiment: dict[str, BriefPersonSentiment]
    recent_decisions: list[BriefRecentDecision]
    context_excerpts: list[BriefContextExcerpt]
    suggested_start: BriefSuggestedStart
    summary: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_people(session: Session, person_ids: list[str]) -> list[PersonORM]:
    if not person_ids:
        return []
    rows = (
        session.query(PersonORM)
        .filter(PersonORM.person_id.in_(person_ids))
        .all()
    )
    # Preserve caller order.
    by_id = {p.person_id: p for p in rows}
    return [by_id[pid] for pid in person_ids if pid in by_id]


def _meetings_with_any_attendee(
    session: Session,
    person_ids: list[str],
    *,
    meeting_type: Optional[str] = None,
) -> list[MeetingORM]:
    """Return meetings that include at least one of the given attendees."""
    if not person_ids:
        return []
    query = (
        session.query(MeetingORM)
        .join(
            meeting_attendees,
            MeetingORM.meeting_id == meeting_attendees.c.meeting_id,
        )
        .filter(meeting_attendees.c.person_id.in_(person_ids))
    )
    if meeting_type:
        query = query.filter(MeetingORM.meeting_type == meeting_type)
    return query.distinct().all()


def _parse_due(due: Optional[str]) -> Optional[datetime]:
    if not due:
        return None
    try:
        return datetime.fromisoformat(due).replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            return datetime.strptime(due, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None


def _infer_meeting_type(meetings: list[MeetingORM]) -> Optional[str]:
    """Infer a typical meeting type from the attendee-set history.

    Returns the most common type across the supplied meetings, or None.
    """
    if not meetings:
        return None
    counts: dict[str, int] = {}
    for m in meetings:
        if m.meeting_type:
            counts[m.meeting_type] = counts.get(m.meeting_type, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: kv[1])[0]


def _generate_title(meeting_type: str | None, names: list[str]) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    nice_type = {
        "one_on_one": "1:1",
        "standup": "Standup",
        "team_meeting": "Team meeting",
        "customer_meeting": "Customer meeting",
        "decision_meeting": "Decision meeting",
        "brainstorm": "Brainstorm",
        "retrospective": "Retrospective",
        "planning": "Planning",
    }.get(meeting_type or "", (meeting_type or "Meeting").replace("_", " ").title())

    shown = [n for n in names if n][:3]
    if shown:
        joined = " & ".join(shown)
        return f"{nice_type} with {joined} — {today}"
    return f"{nice_type} — {today}"


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------


def _build_who_and_when_last(
    session: Session,
    people: list[PersonORM],
    meetings: list[MeetingORM],
) -> BriefWhoAndWhenLast:
    """Section 1 — attendee cards, last meeting, cadence, optional series card."""
    meetings_sorted = sorted(
        [m for m in meetings if m.date],
        key=lambda m: m.date,
        reverse=True,
    )

    attendees: list[BriefAttendee] = []
    for p in people:
        attendees.append(
            BriefAttendee(
                person_id=p.person_id,
                name=p.name,
                email=p.email,
                # SPK-1: we don't have a stable person→label mapping at query
                # time, so leave blank. The record panel pre-fills names
                # directly from the attendee list.
                last_speaker_label=None,
            )
        )

    last_meeting_id = None
    last_meeting_date = None
    last_meeting_title = None
    cadence = None
    if meetings_sorted:
        last = meetings_sorted[0]
        last_meeting_id = last.meeting_id
        last_meeting_date = last.date.isoformat() if last.date else None
        last_meeting_title = last.title
        if len(meetings_sorted) >= 3:
            # Crude cadence from inter-meeting intervals across the most
            # recent four meetings (same classifier as REC-1).
            from meeting_minutes.system3.series import classify_cadence

            dates = [m.date for m in meetings_sorted[:8] if m.date]
            cadence = classify_cadence(list(reversed(dates)))

    # Surface a series card if a series exists for the *exact* attendee set.
    series_card: BriefSeriesCard | None = None
    if people:
        from meeting_minutes.system3.db import MeetingSeriesMemberORM, MeetingSeriesORM

        hash_ = compute_attendee_hash([p.person_id for p in people])
        series_row = (
            session.query(MeetingSeriesORM)
            .filter_by(attendee_hash=hash_)
            .first()
        )
        if series_row is not None:
            member_count = (
                session.query(MeetingSeriesMemberORM)
                .filter_by(series_id=series_row.series_id)
                .count()
            )
            series_card = BriefSeriesCard(
                series_id=series_row.series_id,
                title=series_row.title,
                meeting_type=series_row.meeting_type,
                cadence=series_row.cadence,
                member_count=member_count,
            )

    return BriefWhoAndWhenLast(
        attendees=attendees,
        last_meeting_id=last_meeting_id,
        last_meeting_date=last_meeting_date,
        last_meeting_title=last_meeting_title,
        cadence=cadence,
        series=series_card,
        total_prior_meetings=len(meetings_sorted),
    )


def _build_open_commitments(
    session: Session,
    people: list[PersonORM],
    meetings: list[MeetingORM],
) -> list[BriefOpenCommitment]:
    """Section 2 — open action items for attendees, overdue flagged."""
    if not people or not meetings:
        return []

    owner_names = {p.name for p in people if p.name}
    meeting_ids = {m.meeting_id for m in meetings}
    meeting_date_by_id = {m.meeting_id: m.date for m in meetings}

    rows = (
        session.query(ActionItemORM)
        .filter(
            ActionItemORM.meeting_id.in_(meeting_ids),
            ActionItemORM.owner.in_(owner_names),
            ActionItemORM.status != "done",
        )
        .all()
    )

    now = datetime.now(timezone.utc)
    out: list[BriefOpenCommitment] = []
    for ai in rows:
        due = _parse_due(ai.due_date)
        overdue = bool(due is not None and due < now)
        md = meeting_date_by_id.get(ai.meeting_id)
        out.append(
            BriefOpenCommitment(
                action_id=ai.action_item_id,
                owner=ai.owner,
                description=ai.description or "",
                due_date=ai.due_date,
                overdue=overdue,
                meeting_id=ai.meeting_id,
                meeting_date=md.isoformat() if md else None,
            )
        )

    # Overdue first, then by due date ascending, then newest meeting first.
    out.sort(
        key=lambda c: (
            not c.overdue,
            c.due_date or "9999-99-99",
            -(datetime.fromisoformat(c.meeting_date).timestamp() if c.meeting_date else 0.0),
        )
    )
    return out


def _build_unresolved_topics(
    session: Session,
    meetings: list[MeetingORM],
    *,
    limit: int = 15,
) -> list[BriefUnresolvedTopic]:
    """Section 3 — recent parking-lot / discussion-point rows grouped by text."""
    if not meetings:
        return []

    meeting_ids = {m.meeting_id for m in meetings}
    meeting_date_by_id = {m.meeting_id: m.date for m in meetings}

    chunks = (
        session.query(EmbeddingChunkORM)
        .filter(
            EmbeddingChunkORM.meeting_id.in_(meeting_ids),
            EmbeddingChunkORM.chunk_type.in_(("parking_lot", "discussion_point")),
        )
        .all()
    )

    buckets: dict[str, dict] = {}
    for c in chunks:
        text = (c.text or "").strip()
        if not text:
            continue
        norm = text.split(".")[0].strip().lower()[:80]
        if not norm:
            continue
        bucket = buckets.setdefault(
            norm,
            {"text": text[:200], "meeting_ids": set(), "dates": []},
        )
        bucket["meeting_ids"].add(c.meeting_id)
        md = meeting_date_by_id.get(c.meeting_id)
        if md:
            bucket["dates"].append(md)

    out: list[BriefUnresolvedTopic] = []
    for b in buckets.values():
        dates_sorted = sorted(b["dates"])
        out.append(
            BriefUnresolvedTopic(
                text=b["text"],
                first_mentioned=dates_sorted[0].isoformat() if dates_sorted else None,
                last_mentioned=dates_sorted[-1].isoformat() if dates_sorted else None,
                meeting_ids=sorted(b["meeting_ids"]),
            )
        )
    # Newest last-mentioned first.
    out.sort(key=lambda t: t.last_mentioned or "", reverse=True)
    return out[:limit]


def _build_recent_sentiment(
    session: Session,
    people: list[PersonORM],
    meetings: list[MeetingORM],
    *,
    limit: int = 5,
) -> dict[str, BriefPersonSentiment]:
    """Section 4 — per-person sentiment timeseries over the last N meetings."""
    if not people or not meetings:
        return {}

    meetings_sorted = sorted(
        [m for m in meetings if m.date],
        key=lambda m: m.date,
        reverse=True,
    )[:limit]
    meetings_sorted.reverse()  # oldest→newest in the sparkline

    out: dict[str, BriefPersonSentiment] = {}
    for p in people:
        points: list[BriefSentimentPoint] = []
        for m in meetings_sorted:
            minutes = m.minutes
            if minutes is None:
                continue
            sentiment_val: str | None = None
            if minutes.structured_json:
                try:
                    structured = json.loads(minutes.structured_json)
                except (json.JSONDecodeError, TypeError):
                    structured = None
                if structured:
                    for prt in structured.get("participants") or []:
                        if not isinstance(prt, dict):
                            continue
                        if (prt.get("name") or "").lower() == p.name.lower():
                            sentiment_val = prt.get("sentiment")
                            break
                    if sentiment_val is None:
                        # Fall back to meeting-level sentiment.
                        sentiment_val = structured.get("sentiment")
            if sentiment_val is None:
                sentiment_val = minutes.sentiment
            if sentiment_val is None:
                continue
            score = SENTIMENT_SCORES.get(sentiment_val.lower())
            if score is None:
                continue
            points.append(
                BriefSentimentPoint(
                    date=m.date.isoformat() if m.date else None,
                    score=score,
                    sentiment=sentiment_val,
                    meeting_id=m.meeting_id,
                )
            )
        if points:
            out[p.person_id] = BriefPersonSentiment(
                person_id=p.person_id, name=p.name, scores=points,
            )
    return out


def _build_recent_decisions(
    session: Session,
    people: list[PersonORM],
    meetings: list[MeetingORM],
    *,
    limit: int = 10,
) -> list[BriefRecentDecision]:
    """Section 5 — recent decisions involving these attendees."""
    if not people or not meetings:
        return []

    owner_names = {p.name for p in people if p.name}
    meeting_ids = {m.meeting_id for m in meetings}
    meeting_date_by_id = {m.meeting_id: m.date for m in meetings}

    rows = (
        session.query(DecisionORM)
        .filter(DecisionORM.meeting_id.in_(meeting_ids))
        .all()
    )
    rows.sort(
        key=lambda d: meeting_date_by_id.get(d.meeting_id) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    out: list[BriefRecentDecision] = []
    for d in rows:
        # Prefer decisions attributed to an attendee; fall back to any
        # decision made in a meeting that an attendee attended.
        if d.made_by and owner_names and d.made_by not in owner_names:
            # Keep it — attendees attended — but deprioritise later.
            pass
        md = meeting_date_by_id.get(d.meeting_id)
        out.append(
            BriefRecentDecision(
                decision_id=d.decision_id,
                description=d.description or "",
                made_by=d.made_by,
                date=md.isoformat() if md else None,
                rationale=d.rationale,
                meeting_id=d.meeting_id,
            )
        )
    return out[:limit]


def _build_context_excerpts(
    session: Session,
    config: AppConfig,
    people: list[PersonORM],
    meetings: list[MeetingORM],
    *,
    limit: int = 3,
    window_days: int = 90,
) -> list[BriefContextExcerpt]:
    """Section 6 — top-N relevant transcript chunks.

    Uses the embedding engine's ``search`` path without running the chat
    LLM. Falls back to the most recent transcript chunks if semantic
    search fails (e.g. sqlite-vec not loaded in this session).
    """
    if not people or not meetings:
        return []

    cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).strftime("%Y-%m-%d")
    recent_meeting_ids = {
        m.meeting_id
        for m in meetings
        if m.date and m.date.strftime("%Y-%m-%d") >= cutoff
    }
    if not recent_meeting_ids:
        recent_meeting_ids = {m.meeting_id for m in meetings}

    meeting_date_by_id = {m.meeting_id: m.date for m in meetings}
    meeting_title_by_id = {m.meeting_id: m.title for m in meetings}

    # Build a simple retrieval query from attendee names.
    query_text = " ".join(p.name for p in people if p.name)[:200]

    try:
        from meeting_minutes.embeddings import EmbeddingEngine

        engine = EmbeddingEngine(config)
        results = engine.search(
            query=query_text or "recent discussion",
            session=session,
            limit=limit * 3,
            after_date=cutoff,
        )
    except Exception as exc:  # pragma: no cover - fallback
        logger.debug("Embedding search unavailable in brief: %s", exc)
        results = []

    out: list[BriefContextExcerpt] = []
    if results:
        for r in results:
            mid = r.get("meeting_id")
            if mid and mid not in recent_meeting_ids:
                continue
            md = meeting_date_by_id.get(mid)
            out.append(
                BriefContextExcerpt(
                    meeting_id=mid,
                    date=md.isoformat() if md else r.get("meeting_date"),
                    title=meeting_title_by_id.get(mid),
                    chunk_text=r.get("text", "")[:600],
                    chunk_type=r.get("chunk_type"),
                    score=float(r.get("distance", 0.0)) if r.get("distance") is not None else None,
                )
            )
            if len(out) >= limit:
                break

    if not out:
        # Fallback: grab the newest summary/discussion chunk per recent meeting.
        chunks = (
            session.query(EmbeddingChunkORM)
            .filter(
                EmbeddingChunkORM.meeting_id.in_(recent_meeting_ids),
                EmbeddingChunkORM.chunk_type.in_(("summary", "discussion_point")),
            )
            .all()
        )
        # Sort by meeting_date descending.
        chunks.sort(
            key=lambda c: meeting_date_by_id.get(c.meeting_id) or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        seen_meetings: set[str] = set()
        for c in chunks:
            if c.meeting_id in seen_meetings:
                continue
            seen_meetings.add(c.meeting_id)
            md = meeting_date_by_id.get(c.meeting_id)
            out.append(
                BriefContextExcerpt(
                    meeting_id=c.meeting_id,
                    date=md.isoformat() if md else c.meeting_date,
                    title=meeting_title_by_id.get(c.meeting_id),
                    chunk_text=(c.text or "")[:600],
                    chunk_type=c.chunk_type,
                    score=None,
                )
            )
            if len(out) >= limit:
                break

    return out


def _build_suggested_start(
    people: list[PersonORM],
    meeting_type: str | None,
    open_commitments: list[BriefOpenCommitment],
) -> BriefSuggestedStart:
    """Section 7 — the Start Recording panel payload."""
    names = [p.name for p in people]
    title = _generate_title(meeting_type, names)

    if open_commitments:
        carry_lines = ["## Carry-forward — open commitments", ""]
        for c in open_commitments[:8]:
            owner = f" ({c.owner})" if c.owner else ""
            due = f" — due {c.due_date}" if c.due_date else ""
            overdue = " ⚠ overdue" if c.overdue else ""
            carry_lines.append(f"- [ ] {c.description}{owner}{due}{overdue}")
        carry_note = "\n".join(carry_lines)
    else:
        carry_note = "## Carry-forward\n\n- (no open commitments)"

    return BriefSuggestedStart(
        title=title,
        meeting_type=meeting_type or "other",
        attendee_labels=names,
        carry_forward_note=carry_note,
    )


# ---------------------------------------------------------------------------
# Optional LLM summarization
# ---------------------------------------------------------------------------


async def _maybe_summarize(
    config: AppConfig,
    payload: BriefingPayload,
) -> str | None:
    """Run a single 2-sentence LLM synthesis over the aggregates. Returns
    ``None`` if disabled or if the LLM call fails — the caller treats a
    missing summary as "no synthesis requested"."""
    if not config.brief.summarize_with_llm:
        return None

    # Build a compact prompt from the structured payload.
    bullets: list[str] = []
    names = ", ".join(p.name for p in payload.people)
    bullets.append(f"Attendees: {names}")
    if payload.who_and_when_last.cadence:
        bullets.append(f"Cadence: {payload.who_and_when_last.cadence}")
    if payload.open_commitments:
        bullets.append(f"{len(payload.open_commitments)} open commitments ({sum(1 for c in payload.open_commitments if c.overdue)} overdue)")
    if payload.recent_decisions:
        bullets.append(f"{len(payload.recent_decisions)} recent decisions")
    if payload.unresolved_topics:
        bullets.append(f"{len(payload.unresolved_topics)} unresolved topics")

    prompt = (
        "You are an assistant helping the user prepare for a meeting. "
        "Given the following pre-meeting signals, write exactly two short, "
        "action-oriented sentences summarising what the user should be ready "
        "to discuss. Do not use bullet points. Do not mention numbers "
        "directly; instead name the topic areas at stake.\n\n"
        + "\n".join(f"- {b}" for b in bullets)
    )
    try:
        from meeting_minutes.system2.llm_client import LLMClient

        client = LLMClient(config.generation.llm)
        response = await client.generate(prompt=prompt)
        return (response.text or "").strip() or None
    except Exception as exc:  # pragma: no cover - network/key failures
        logger.debug("Briefing summary LLM call failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.get("", response_model=BriefingPayload)
async def get_briefing(
    session: Annotated[Session, Depends(get_db_session)],
    config: Annotated[AppConfig, Depends(get_config)],
    people: list[str] = Query(default=[], alias="people"),
    type: Optional[str] = Query(default=None, alias="type"),
) -> BriefingPayload:
    """Return a pre-meeting briefing payload for the given attendee set."""
    person_ids = [pid for pid in (people or []) if pid]
    if not person_ids:
        raise HTTPException(
            status_code=400,
            detail="Provide at least one ?people=<person_id> query parameter.",
        )

    resolved = _resolve_people(session, person_ids)
    if not resolved:
        raise HTTPException(
            status_code=404,
            detail=f"No persons found for the given ids: {', '.join(person_ids)}",
        )

    all_meetings = _meetings_with_any_attendee(session, [p.person_id for p in resolved])
    typed_meetings = (
        [m for m in all_meetings if m.meeting_type == type]
        if type
        else all_meetings
    )

    # Fall back to un-typed meetings for history if the type filter removes everything.
    hist_meetings = typed_meetings if typed_meetings else all_meetings

    inferred_type = type or _infer_meeting_type(all_meetings)

    who = _build_who_and_when_last(session, resolved, hist_meetings)
    opens = _build_open_commitments(session, resolved, all_meetings)
    topics = _build_unresolved_topics(session, hist_meetings)
    sentiment = _build_recent_sentiment(session, resolved, hist_meetings)
    decisions = _build_recent_decisions(session, resolved, hist_meetings)
    excerpts = _build_context_excerpts(session, config, resolved, hist_meetings)
    suggested = _build_suggested_start(resolved, inferred_type, opens)

    payload = BriefingPayload(
        people=[
            BriefAttendee(person_id=p.person_id, name=p.name, email=p.email)
            for p in resolved
        ],
        meeting_type=inferred_type,
        who_and_when_last=who,
        open_commitments=opens,
        unresolved_topics=topics,
        recent_sentiment=sentiment,
        recent_decisions=decisions,
        context_excerpts=excerpts,
        suggested_start=suggested,
    )

    if config.brief.summarize_with_llm:
        summary = await _maybe_summarize(config, payload)
        if summary:
            payload.summary = summary

    return payload
