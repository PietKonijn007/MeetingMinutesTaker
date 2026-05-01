"""Tests for recurring-meeting threading (REC-1)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from meeting_minutes.system3.db import (
    ActionItemORM,
    DecisionORM,
    EmbeddingChunkORM,
    MeetingORM,
    MeetingSeriesMemberORM,
    MeetingSeriesORM,
    PersonORM,
    get_session_factory,
    meeting_attendees,
)
from meeting_minutes.system3.series import (
    classify_cadence,
    compute_attendee_hash,
    detect_and_upsert,
    detect_series,
    series_aggregates,
    series_for_meeting,
    upsert_series,
)


@pytest.fixture
def session():
    session_factory = get_session_factory("sqlite:///:memory:")
    s = session_factory()
    yield s
    s.close()


def _mk_person(session, name: str) -> PersonORM:
    p = PersonORM(person_id=f"p-{uuid.uuid4().hex[:8]}", name=name)
    session.add(p)
    session.commit()
    return p


def _mk_meeting(
    session,
    title: str,
    meeting_type: str,
    date: datetime,
    attendees: list[PersonORM],
) -> MeetingORM:
    m = MeetingORM(
        meeting_id=f"m-{uuid.uuid4().hex[:8]}",
        title=title,
        date=date,
        meeting_type=meeting_type,
        status="final",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        duration="30 minutes",
    )
    session.add(m)
    session.flush()
    for a in attendees:
        session.execute(
            meeting_attendees.insert().values(meeting_id=m.meeting_id, person_id=a.person_id)
        )
    session.commit()
    return m


def test_attendee_hash_is_order_independent():
    h1 = compute_attendee_hash(["p-1", "p-2", "p-3"])
    h2 = compute_attendee_hash(["p-3", "p-1", "p-2"])
    assert h1 == h2


def test_classify_cadence_buckets():
    now = datetime.now(timezone.utc)
    weekly = [now + timedelta(days=7 * i) for i in range(4)]
    biweekly = [now + timedelta(days=14 * i) for i in range(4)]
    monthly = [now + timedelta(days=30 * i) for i in range(4)]
    # Median interval 60 days = outside any bucket.
    irregular = [now, now + timedelta(days=60), now + timedelta(days=120)]

    assert classify_cadence(weekly) == "weekly"
    assert classify_cadence(biweekly) == "biweekly"
    assert classify_cadence(monthly) == "monthly"
    assert classify_cadence(irregular) == "irregular"
    # Boundary: 14-day intervals = biweekly; 30-day = monthly.
    assert classify_cadence([now + timedelta(days=14 * i) for i in range(3)]) == "biweekly"
    assert classify_cadence([now + timedelta(days=30 * i) for i in range(3)]) == "monthly"


def test_three_meetings_same_attendees_produce_series(session):
    jon = _mk_person(session, "Jon")
    sarah = _mk_person(session, "Sarah")

    base = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)
    for i in range(3):
        _mk_meeting(session, f"1:1 {i}", "one_on_one", base + timedelta(days=7 * i), [jon, sarah])

    summary = detect_and_upsert(session)
    assert len(summary.created) == 1
    assert session.query(MeetingSeriesORM).count() == 1
    row = session.query(MeetingSeriesORM).one()
    assert row.cadence == "weekly"
    assert row.meeting_type == "one_on_one"
    members = session.query(MeetingSeriesMemberORM).filter_by(series_id=row.series_id).all()
    assert len(members) == 3


def test_fourth_meeting_joins_series(session):
    jon = _mk_person(session, "Jon")
    base = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)
    for i in range(3):
        _mk_meeting(session, f"1:1 {i}", "one_on_one", base + timedelta(days=7 * i), [jon])
    detect_and_upsert(session)
    first_detected = session.query(MeetingSeriesORM).one().last_detected_at

    # Add a fourth meeting — re-run and confirm membership grows.
    _mk_meeting(session, "1:1 4", "one_on_one", base + timedelta(days=28), [jon])
    summary = detect_and_upsert(session)
    assert summary.updated  # re-detected with new membership

    row = session.query(MeetingSeriesORM).one()
    assert len(row.members) == 4
    assert row.last_detected_at >= first_detected


def test_detection_idempotent(session):
    jon = _mk_person(session, "Jon")
    base = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)
    for i in range(3):
        _mk_meeting(session, f"x{i}", "standup", base + timedelta(days=7 * i), [jon])

    detect_and_upsert(session)
    first_count = session.query(MeetingSeriesORM).count()
    detect_and_upsert(session)
    detect_and_upsert(session)
    assert session.query(MeetingSeriesORM).count() == first_count == 1


def test_type_change_excludes_from_series(session):
    jon = _mk_person(session, "Jon")
    base = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)
    meetings = [
        _mk_meeting(session, f"x{i}", "standup", base + timedelta(days=7 * i), [jon])
        for i in range(3)
    ]
    detect_and_upsert(session)
    assert session.query(MeetingSeriesORM).count() == 1

    # Flip one member's meeting_type — the group now only has 2 standups,
    # which falls below the threshold — the series should be removed.
    meetings[0].meeting_type = "team_meeting"
    session.commit()
    detect_and_upsert(session)
    assert session.query(MeetingSeriesORM).count() == 0


def test_different_attendee_sets_are_separate_series(session):
    jon = _mk_person(session, "Jon")
    sarah = _mk_person(session, "Sarah")
    base = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)

    for i in range(3):
        _mk_meeting(session, f"J{i}", "one_on_one", base + timedelta(days=7 * i), [jon])
    for i in range(3):
        _mk_meeting(session, f"S{i}", "one_on_one", base + timedelta(days=7 * i), [sarah])

    summary = detect_and_upsert(session)
    assert len(summary.created) == 2
    assert session.query(MeetingSeriesORM).count() == 2


def test_series_aggregates(session):
    jon = _mk_person(session, "Jon")
    base = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)
    meetings = [
        _mk_meeting(session, f"1:1 {i}", "one_on_one", base + timedelta(days=7 * i), [jon])
        for i in range(3)
    ]

    # Seed action items + decisions across members.
    for idx, m in enumerate(meetings):
        session.add(
            ActionItemORM(
                action_item_id=f"ai-{idx}",
                meeting_id=m.meeting_id,
                description=f"Ship feature {idx}",
                owner="Jon",
                status="open",
                proposal_state="confirmed",
            )
        )
        session.add(
            DecisionORM(
                decision_id=f"d-{idx}",
                meeting_id=m.meeting_id,
                description=f"Decision {idx}",
                made_by="Jon",
            )
        )
    # One closed action to show the filter excludes it.
    session.add(
        ActionItemORM(
            action_item_id="ai-done",
            meeting_id=meetings[0].meeting_id,
            description="Closed",
            owner="Jon",
            status="done",
            proposal_state="confirmed",
        )
    )
    # Seed parking-lot chunks — same text across 2 meetings → recurring.
    for m in meetings[:2]:
        session.add(
            EmbeddingChunkORM(
                meeting_id=m.meeting_id,
                chunk_type="parking_lot",
                text="Latency on the checkout flow",
                meeting_date=m.date.strftime("%Y-%m-%d"),
                created_at=datetime.now(timezone.utc),
            )
        )
    session.commit()

    detect_and_upsert(session)
    series = session.query(MeetingSeriesORM).one()
    agg = series_aggregates(session, series.series_id)
    assert len(agg.open_action_items) == 3  # closed one excluded
    assert len(agg.recent_decisions) == 3
    assert any("Latency" in t["topic_summary"] for t in agg.recurring_topics)


def test_series_for_meeting_returns_row(session):
    jon = _mk_person(session, "Jon")
    base = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)
    meetings = [
        _mk_meeting(session, f"1:1 {i}", "one_on_one", base + timedelta(days=7 * i), [jon])
        for i in range(3)
    ]
    detect_and_upsert(session)
    s = series_for_meeting(session, meetings[1].meeting_id)
    assert s is not None
    assert s.meeting_type == "one_on_one"

    # Unknown meeting → None.
    assert series_for_meeting(session, "m-unknown") is None


def test_below_threshold_no_series(session):
    jon = _mk_person(session, "Jon")
    base = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)
    _mk_meeting(session, "one", "one_on_one", base, [jon])
    _mk_meeting(session, "two", "one_on_one", base + timedelta(days=7), [jon])
    summary = detect_and_upsert(session)
    assert session.query(MeetingSeriesORM).count() == 0
    assert summary.created == [] and summary.updated == []
