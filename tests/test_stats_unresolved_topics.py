"""Tests for ANA-1 Panel 2 — recurring unresolved topics."""

from __future__ import annotations

import struct
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from meeting_minutes.stats_analytics import (
    rebuild_topic_clusters_cache,
    unresolved_topics,
)
from meeting_minutes.system3.db import (
    EmbeddingChunkORM,
    MeetingORM,
    TopicClusterCacheORM,
    get_session_factory,
)
from sqlalchemy import text as sql_text


@pytest.fixture
def session():
    sf = get_session_factory("sqlite:///:memory:")
    s = sf()
    yield s
    s.close()


def _seed_meeting(session, idx: int) -> MeetingORM:
    m = MeetingORM(
        meeting_id=f"m-{idx}",
        title=f"m {idx}",
        date=datetime.now(timezone.utc),
        meeting_type="standup",
        status="final",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session.add(m)
    session.commit()
    return m


def _seed_chunk(
    session,
    *,
    meeting_id: str,
    chunk_type: str,
    text: str,
    vector: list[float],
) -> int:
    chunk = EmbeddingChunkORM(
        meeting_id=meeting_id,
        chunk_type=chunk_type,
        text=text,
        meeting_date=None,
        created_at=datetime.now(timezone.utc),
    )
    session.add(chunk)
    session.flush()
    # Pad/truncate to 384 dims to match the virtual table schema.
    padded = list(vector) + [0.0] * (384 - len(vector))
    padded = padded[:384]
    vec_bytes = struct.pack(f"{len(padded)}f", *padded)
    session.execute(
        sql_text(
            "INSERT INTO embedding_vectors (chunk_id, embedding) VALUES (:cid, vec_f32(:vec))"
        ),
        {"cid": chunk.chunk_id, "vec": vec_bytes},
    )
    session.commit()
    return chunk.chunk_id


def _sqlite_vec_available(session) -> bool:
    try:
        session.execute(sql_text("SELECT vec_version()")).scalar()
        return True
    except Exception:
        return False


def test_cluster_across_three_meetings(session):
    if not _sqlite_vec_available(session):
        pytest.skip("sqlite-vec not available")

    meetings = [_seed_meeting(session, i) for i in range(3)]
    # All three chunks share the same normalized vector → they cluster.
    vec = [1.0, 0.0, 0.0]
    for m in meetings:
        _seed_chunk(
            session,
            meeting_id=m.meeting_id,
            chunk_type="parking_lot",
            text="Latency in checkout flow",
            vector=vec,
        )

    result = rebuild_topic_clusters_cache(session)
    assert result["cluster_count"] >= 1

    topics = unresolved_topics(session, min_count=3, auto_rebuild_hours=9999)
    assert len(topics["clusters"]) == 1
    c = topics["clusters"][0]
    assert c["meeting_count"] == 3
    assert "checkout" in c["topic_summary"].lower()


def test_resolved_cluster_is_filtered(session):
    if not _sqlite_vec_available(session):
        pytest.skip("sqlite-vec not available")

    meetings = [_seed_meeting(session, i) for i in range(3)]
    vec = [1.0, 0.0, 0.0]
    for m in meetings:
        _seed_chunk(
            session,
            meeting_id=m.meeting_id,
            chunk_type="parking_lot",
            text="Latency in checkout flow",
            vector=vec,
        )
    # Seed a decision chunk with the same vector in one meeting → the
    # cluster is considered resolved.
    _seed_chunk(
        session,
        meeting_id=meetings[0].meeting_id,
        chunk_type="decision",
        text="We will split the checkout service",
        vector=vec,
    )

    rebuild_topic_clusters_cache(session)
    topics = unresolved_topics(session, min_count=3, auto_rebuild_hours=9999)
    assert topics["clusters"] == []


def test_disabled_when_sqlite_vec_missing(session):
    # Force the vec availability check to return False.
    with patch("meeting_minutes.stats_analytics._sqlite_vec_available", return_value=False):
        result = unresolved_topics(session, min_count=3)
    assert result["clusters"] == []
    assert "sqlite-vec" in (result.get("disabled_reason") or "")


def test_rebuild_idempotent(session):
    if not _sqlite_vec_available(session):
        pytest.skip("sqlite-vec not available")

    meetings = [_seed_meeting(session, i) for i in range(3)]
    vec = [1.0, 0.0, 0.0]
    for m in meetings:
        _seed_chunk(
            session,
            meeting_id=m.meeting_id,
            chunk_type="parking_lot",
            text="Latency in checkout flow",
            vector=vec,
        )

    rebuild_topic_clusters_cache(session)
    first = session.query(TopicClusterCacheORM).count()
    rebuild_topic_clusters_cache(session)
    rebuild_topic_clusters_cache(session)
    assert session.query(TopicClusterCacheORM).count() == first
