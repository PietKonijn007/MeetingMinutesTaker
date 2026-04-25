"""Cross-meeting analytics (ANA-1).

Pure SQL aggregates over existing tables — commitment completion per
person, per-meeting-type effectiveness, sentiment trends — plus the
``topic_clusters_cache`` rebuild for Panel 2.

Graceful degradation: the topic-cluster panel falls back to an empty
result set when ``sqlite-vec`` is unavailable, so the other three panels
still render on a stock SQLite install.
"""

from __future__ import annotations

import json
import logging
import struct
import uuid
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy import text as sql_text

from meeting_minutes.system3.db import (
    ActionItemORM,
    DecisionORM,
    EmbeddingChunkORM,
    MeetingORM,
    MeetingSeriesMemberORM,
    MinutesORM,
    PersonORM,
    TopicClusterCacheORM,
    meeting_attendees,
)

logger = logging.getLogger(__name__)

# Sentiment → numeric mapping (shared across panels + tests).
SENTIMENT_SCORES: dict[str, float] = {
    "positive": 1.0,
    "constructive": 0.7,
    "neutral": 0.5,
    "mixed": 0.5,
    "tense": 0.3,
    "negative": 0.0,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _series_meeting_ids(session: Session, series_id: str) -> list[str]:
    return [
        m.meeting_id
        for m in session.query(MeetingSeriesMemberORM)
        .filter_by(series_id=series_id)
        .all()
    ]


def _filter_meetings(
    session: Session,
    *,
    days: Optional[int] = None,
    meeting_type: Optional[str] = None,
    series_id: Optional[str] = None,
) -> list[MeetingORM]:
    query = session.query(MeetingORM)
    if days:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        query = query.filter(MeetingORM.date >= cutoff)
    if meeting_type:
        query = query.filter(MeetingORM.meeting_type == meeting_type)
    if series_id:
        ids = _series_meeting_ids(session, series_id)
        if not ids:
            return []
        query = query.filter(MeetingORM.meeting_id.in_(ids))
    return query.all()


def _week_start(dt: datetime) -> str:
    monday = dt - timedelta(days=dt.weekday())
    return monday.strftime("%Y-%m-%d")


def _parse_due(due: Optional[str]) -> Optional[datetime]:
    """Parse an action item's ``due_date`` field (best-effort)."""
    if not due:
        return None
    try:
        return datetime.fromisoformat(due).replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            return datetime.strptime(due, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None


# ---------------------------------------------------------------------------
# Panel 1 — Commitments per person
# ---------------------------------------------------------------------------


def commitments_per_person(
    session: Session,
    *,
    days: int = 90,
    meeting_type: Optional[str] = None,
    series_id: Optional[str] = None,
) -> dict:
    """Per-person action-item counts over a rolling window."""
    meetings = _filter_meetings(
        session, days=days, meeting_type=meeting_type, series_id=series_id
    )
    if not meetings:
        return {"persons": []}

    meeting_ids = {m.meeting_id for m in meetings}
    meeting_date_by_id = {m.meeting_id: m.date for m in meetings if m.date}

    # People from DB — we match on owner string (existing convention).
    persons = session.query(PersonORM).all()
    # Include owners that appear as strings but aren't in persons (rare).
    action_rows = (
        session.query(ActionItemORM)
        .filter(ActionItemORM.meeting_id.in_(meeting_ids))
        .filter(ActionItemORM.proposal_state == "confirmed")
        .all()
    )

    now = datetime.now(timezone.utc)

    # Build the 12-week buckets anchored at this week's Monday.
    week_keys: list[str] = []
    for i in range(12):
        week_keys.append(_week_start(now - timedelta(weeks=11 - i)))

    # Aggregate by owner name.
    buckets: dict[str, dict] = {}
    for person in persons:
        buckets[person.name] = {
            "person_id": person.person_id,
            "name": person.name,
            "assigned": 0,
            "completed": 0,
            "overdue": 0,
            "completed_per_week": {k: 0 for k in week_keys},
        }

    for ai in action_rows:
        owner = ai.owner or None
        if not owner:
            continue
        bucket = buckets.get(owner)
        if bucket is None:
            # Orphan owner string — surface as a person row anyway.
            bucket = buckets.setdefault(
                owner,
                {
                    "person_id": None,
                    "name": owner,
                    "assigned": 0,
                    "completed": 0,
                    "overdue": 0,
                    "completed_per_week": {k: 0 for k in week_keys},
                },
            )

        bucket["assigned"] += 1
        if ai.status == "done":
            bucket["completed"] += 1
            md = meeting_date_by_id.get(ai.meeting_id)
            if md is not None:
                key = _week_start(md)
                if key in bucket["completed_per_week"]:
                    bucket["completed_per_week"][key] += 1

        # Overdue = past due date AND not done.
        if (ai.status or "open") != "done":
            due = _parse_due(ai.due_date)
            if due is not None and due < now:
                bucket["overdue"] += 1

    persons_out = []
    for b in buckets.values():
        if b["assigned"] == 0:
            continue
        rate = b["completed"] / b["assigned"] if b["assigned"] else 0.0
        persons_out.append(
            {
                "person_id": b["person_id"],
                "name": b["name"],
                "assigned": b["assigned"],
                "completed": b["completed"],
                "overdue": b["overdue"],
                "completion_rate": round(rate, 3),
                "sparkline": [b["completed_per_week"][k] for k in week_keys],
            }
        )
    persons_out.sort(key=lambda p: (-p["assigned"], p["name"]))
    return {"persons": persons_out}


# ---------------------------------------------------------------------------
# Panel 3 — Sentiment trend
# ---------------------------------------------------------------------------


def _minutes_structured(row: MinutesORM) -> Optional[dict]:
    if not row or not row.structured_json:
        return None
    try:
        return json.loads(row.structured_json)
    except (json.JSONDecodeError, TypeError):
        return None


def sentiment_trend(
    session: Session,
    *,
    days: int = 90,
    person: Optional[str] = None,
    meeting_type: Optional[str] = None,
    series_id: Optional[str] = None,
) -> dict:
    """Sentiment timeseries over meetings.

    If ``person`` is given (person_id or name), pick that person's
    participant sentiment out of the structured minutes. Otherwise use
    the meeting-level sentiment string.
    """
    meetings = _filter_meetings(
        session, days=days, meeting_type=meeting_type, series_id=series_id
    )
    if not meetings:
        return {"series": []}

    meetings.sort(key=lambda m: m.date or datetime.min.replace(tzinfo=timezone.utc))

    person_name: Optional[str] = None
    if person:
        # Accept either person_id or raw name.
        p = session.get(PersonORM, person)
        person_name = p.name if p is not None else person

    out: list[dict] = []
    for m in meetings:
        minutes = m.minutes  # MinutesORM
        if minutes is None:
            continue

        sentiment_val: Optional[str] = None
        if person_name:
            structured = _minutes_structured(minutes)
            if structured:
                for p in structured.get("participants") or []:
                    if isinstance(p, dict) and (p.get("name") or "").lower() == person_name.lower():
                        sentiment_val = p.get("sentiment")
                        break
            if sentiment_val is None:
                continue
        else:
            sentiment_val = minutes.sentiment
            if sentiment_val is None:
                structured = _minutes_structured(minutes)
                if structured:
                    sentiment_val = structured.get("sentiment")

        if sentiment_val is None:
            continue
        score = SENTIMENT_SCORES.get(sentiment_val.lower())
        if score is None:
            continue

        out.append(
            {
                "meeting_id": m.meeting_id,
                "title": m.title,
                "meeting_type": m.meeting_type,
                "date": m.date.isoformat() if m.date else None,
                "sentiment": sentiment_val,
                "sentiment_score": score,
            }
        )
    return {"series": out}


# ---------------------------------------------------------------------------
# Panel 4 — Meeting-type effectiveness
# ---------------------------------------------------------------------------


def effectiveness_by_type(
    session: Session,
    *,
    days: Optional[int] = None,
    meeting_type: Optional[str] = None,
    series_id: Optional[str] = None,
) -> dict:
    """Per-type % of meetings with each effectiveness attribute set."""
    meetings = _filter_meetings(
        session, days=days, meeting_type=meeting_type, series_id=series_id
    )
    if not meetings:
        return {"types": []}

    buckets: dict[str, dict] = {}
    for m in meetings:
        key = m.meeting_type or "other"
        b = buckets.setdefault(
            key,
            {
                "type": key,
                "meeting_count": 0,
                "clear_agenda": 0,
                "decisions": 0,
                "actions": 0,
                "unresolved": 0,
                "total_with_data": 0,
            },
        )
        b["meeting_count"] += 1

        if m.minutes is None:
            continue
        structured = _minutes_structured(m.minutes)
        if not structured:
            continue
        eff = structured.get("meeting_effectiveness")
        if not isinstance(eff, dict):
            continue
        b["total_with_data"] += 1

        if eff.get("had_clear_agenda"):
            b["clear_agenda"] += 1
        if (eff.get("decisions_made") or 0) > 0:
            b["decisions"] += 1
        if (eff.get("action_items_assigned") or 0) > 0:
            b["actions"] += 1
        if (eff.get("unresolved_items") or 0) > 0:
            b["unresolved"] += 1

    out = []
    for b in buckets.values():
        denom = max(b["total_with_data"], 1)
        out.append(
            {
                "type": b["type"],
                "meeting_count": b["meeting_count"],
                "had_clear_agenda_pct": round(b["clear_agenda"] / denom, 3) if b["total_with_data"] else 0.0,
                "decisions_made_pct": round(b["decisions"] / denom, 3) if b["total_with_data"] else 0.0,
                "action_items_assigned_pct": round(b["actions"] / denom, 3) if b["total_with_data"] else 0.0,
                "unresolved_items_pct": round(b["unresolved"] / denom, 3) if b["total_with_data"] else 0.0,
            }
        )
    out.sort(key=lambda t: -t["meeting_count"])
    return {"types": out}


# ---------------------------------------------------------------------------
# Panel 2 — Recurring unresolved topics (topic clusters + rebuild)
# ---------------------------------------------------------------------------


def _sqlite_vec_available(session: Session) -> bool:
    try:
        session.execute(sql_text("SELECT vec_version()")).scalar()
        return True
    except Exception:
        return False


def _fetch_vector(session: Session, chunk_id: int) -> Optional[list[float]]:
    try:
        row = session.execute(
            sql_text("SELECT embedding FROM embedding_vectors WHERE chunk_id = :cid"),
            {"cid": chunk_id},
        ).fetchone()
    except Exception:
        return None
    if not row:
        return None
    blob = row[0]
    dim = len(blob) // 4
    return list(struct.unpack(f"{dim}f", blob))


def _knn_chunk_ids(
    session: Session, chunk_id: int, k: int, min_similarity: float
) -> list[int]:
    """Return chunk_ids within cosine similarity >= ``min_similarity`` of the
    target chunk, excluding the chunk itself.
    """
    vec = _fetch_vector(session, chunk_id)
    if vec is None:
        return []
    vec_bytes = struct.pack(f"{len(vec)}f", *vec)
    try:
        rows = session.execute(
            sql_text(
                "SELECT chunk_id, distance FROM embedding_vectors "
                "WHERE embedding MATCH vec_f32(:qvec) AND k = :k "
                "ORDER BY distance"
            ),
            {"qvec": vec_bytes, "k": k},
        ).fetchall()
    except Exception:
        return []

    neighbors: list[int] = []
    for cid, distance in rows:
        if cid == chunk_id:
            continue
        # sqlite-vec returns L2 distance on normalized vectors;
        # cosine_similarity = 1 - (distance ** 2) / 2.
        cos = 1.0 - (float(distance) ** 2) / 2.0
        if cos >= min_similarity:
            neighbors.append(cid)
    return neighbors


class _UnionFind:
    def __init__(self, items: Iterable[int]):
        self.parent = {i: i for i in items}

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def rebuild_topic_clusters_cache(
    session: Session,
    *,
    min_similarity: float = 0.8,
    k: int = 10,
) -> dict:
    """Rebuild the ``topic_clusters_cache`` table from scratch.

    The algorithm is a simple union-find over KNN neighbours: for each
    ``discussion_point`` / ``parking_lot`` chunk, link it to its nearest
    neighbours above the cosine-similarity floor. Each connected
    component becomes a cluster.

    Idempotent: clears the table first, so running it repeatedly
    converges on the same output.
    """
    if not _sqlite_vec_available(session):
        return {
            "cluster_count": 0,
            "chunk_count": 0,
            "disabled_reason": "sqlite-vec not available",
        }

    chunks = (
        session.query(EmbeddingChunkORM)
        .filter(EmbeddingChunkORM.chunk_type.in_(("discussion_point", "parking_lot")))
        .all()
    )
    session.query(TopicClusterCacheORM).delete()
    session.commit()

    if not chunks:
        return {"cluster_count": 0, "chunk_count": 0}

    uf = _UnionFind([c.chunk_id for c in chunks])
    chunk_ids_set = {c.chunk_id for c in chunks}
    for c in chunks:
        for neighbor_id in _knn_chunk_ids(session, c.chunk_id, k=k, min_similarity=min_similarity):
            if neighbor_id in chunk_ids_set:
                uf.union(c.chunk_id, neighbor_id)

    # Group chunks by root and stringify a stable cluster_id per root.
    groups: dict[int, list[EmbeddingChunkORM]] = {}
    for c in chunks:
        root = uf.find(c.chunk_id)
        groups.setdefault(root, []).append(c)

    now = datetime.now(timezone.utc)
    cluster_count = 0
    for root, members in groups.items():
        if len(members) < 2:
            # A singleton cluster is still written so we can reason about
            # unresolved-topic counts uniformly; Panel 2 filters to
            # clusters with >= 3 meetings separately.
            pass
        cluster_id = f"tc-{uuid.uuid4().hex[:12]}"
        topic_summary = (members[0].text or "")[:140]
        for m in members:
            session.add(
                TopicClusterCacheORM(
                    cluster_id=cluster_id,
                    chunk_id=m.chunk_id,
                    meeting_id=m.meeting_id,
                    topic_summary=topic_summary,
                    rebuilt_at=now,
                )
            )
        cluster_count += 1
    session.commit()
    return {"cluster_count": cluster_count, "chunk_count": len(chunks)}


def _cache_age_hours(session: Session) -> Optional[float]:
    latest = session.query(func.max(TopicClusterCacheORM.rebuilt_at)).scalar()
    if latest is None:
        return None
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - latest).total_seconds() / 3600.0


def unresolved_topics(
    session: Session,
    *,
    days: Optional[int] = None,
    min_count: int = 3,
    series_id: Optional[str] = None,
    auto_rebuild_hours: float = 24.0,
    resolution_threshold: float = 0.7,
) -> dict:
    """Clusters touching >= ``min_count`` meetings with no similar decision.

    Lazy cache: triggers a rebuild if the cache is empty or older than
    ``auto_rebuild_hours``. If sqlite-vec is unavailable, returns an
    empty list with ``disabled_reason``.
    """
    if not _sqlite_vec_available(session):
        return {"clusters": [], "disabled_reason": "sqlite-vec not available"}

    age = _cache_age_hours(session)
    if age is None or age > auto_rebuild_hours:
        rebuild_topic_clusters_cache(session)

    cache_rows = session.query(TopicClusterCacheORM).all()
    if not cache_rows:
        return {"clusters": []}

    # Narrow by meeting filters: cluster must have a member meeting that
    # survives the filter.
    allowed_ids: Optional[set[str]] = None
    if days or series_id:
        allowed_meetings = _filter_meetings(session, days=days, series_id=series_id)
        allowed_ids = {m.meeting_id for m in allowed_meetings}

    meeting_date_by_id = {
        m.meeting_id: m.date
        for m in session.query(MeetingORM).all()
    }

    clusters: dict[str, dict] = {}
    for r in cache_rows:
        if allowed_ids is not None and r.meeting_id not in allowed_ids:
            continue
        c = clusters.setdefault(
            r.cluster_id,
            {
                "cluster_id": r.cluster_id,
                "topic_summary": r.topic_summary,
                "meeting_ids": set(),
                "chunk_ids": [],
                "dates": [],
            },
        )
        c["meeting_ids"].add(r.meeting_id)
        c["chunk_ids"].append(r.chunk_id)
        md = meeting_date_by_id.get(r.meeting_id)
        if md:
            c["dates"].append(md)

    # Filter to clusters with >= min_count meetings.
    filtered = [
        c for c in clusters.values() if len(c["meeting_ids"]) >= min_count
    ]

    # Filter out clusters that have a "similar" decision in any member
    # meeting — cheap: compare the representative chunk's vector to all
    # decision chunks from those meetings.
    resolved_cluster_ids = set()
    for c in filtered:
        rep_chunk_id = c["chunk_ids"][0]
        rep_vec = _fetch_vector(session, rep_chunk_id)
        if rep_vec is None:
            continue
        rep_bytes = struct.pack(f"{len(rep_vec)}f", *rep_vec)

        decision_chunk_ids = [
            row[0]
            for row in session.execute(
                sql_text(
                    "SELECT chunk_id FROM embedding_chunks "
                    "WHERE chunk_type = 'decision' AND meeting_id IN (:mids)"
                    .replace(":mids", ",".join(f"'{m}'" for m in c["meeting_ids"]))
                )
            ).fetchall()
        ]
        if not decision_chunk_ids:
            continue

        try:
            rows = session.execute(
                sql_text(
                    "SELECT chunk_id, distance FROM embedding_vectors "
                    "WHERE embedding MATCH vec_f32(:qvec) AND k = 50 "
                    "ORDER BY distance"
                ),
                {"qvec": rep_bytes},
            ).fetchall()
        except Exception:
            continue

        for cid, distance in rows:
            if cid not in decision_chunk_ids:
                continue
            cos = 1.0 - (float(distance) ** 2) / 2.0
            if cos >= resolution_threshold:
                resolved_cluster_ids.add(c["cluster_id"])
                break

    out = []
    for c in filtered:
        if c["cluster_id"] in resolved_cluster_ids:
            continue
        dates_sorted = sorted(c["dates"])
        out.append(
            {
                "cluster_id": c["cluster_id"],
                "topic_summary": c["topic_summary"],
                "meeting_ids": sorted(c["meeting_ids"]),
                "meeting_count": len(c["meeting_ids"]),
                "first_mentioned": dates_sorted[0].isoformat() if dates_sorted else None,
                "last_mentioned": dates_sorted[-1].isoformat() if dates_sorted else None,
            }
        )
    out.sort(key=lambda r: -r["meeting_count"])
    return {"clusters": out}
