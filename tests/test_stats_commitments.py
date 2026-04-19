"""Tests for ANA-1 Panel 1 — commitment completion per person."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from meeting_minutes.stats_analytics import commitments_per_person
from meeting_minutes.system3.db import (
    ActionItemORM,
    MeetingORM,
    PersonORM,
    get_session_factory,
    meeting_attendees,
)


@pytest.fixture
def session():
    sf = get_session_factory("sqlite:///:memory:")
    s = sf()
    yield s
    s.close()


def _mk_person(session, name):
    p = PersonORM(person_id=f"p-{uuid.uuid4().hex[:8]}", name=name)
    session.add(p)
    session.commit()
    return p


def _mk_meeting(session, *, date, meeting_type="standup"):
    m = MeetingORM(
        meeting_id=f"m-{uuid.uuid4().hex[:8]}",
        title="m",
        date=date,
        meeting_type=meeting_type,
        status="final",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session.add(m)
    session.commit()
    return m


def _mk_action(session, *, meeting_id, owner, status="open", due_date=None):
    ai = ActionItemORM(
        action_item_id=f"ai-{uuid.uuid4().hex[:8]}",
        meeting_id=meeting_id,
        description="x",
        owner=owner,
        status=status,
        due_date=due_date,
    )
    session.add(ai)
    session.commit()
    return ai


def test_completion_rate_math(session):
    alice = _mk_person(session, "Alice")
    bob = _mk_person(session, "Bob")
    now = datetime.now(timezone.utc)
    m = _mk_meeting(session, date=now - timedelta(days=5))

    for _ in range(3):
        _mk_action(session, meeting_id=m.meeting_id, owner="Alice", status="done")
    for _ in range(2):
        _mk_action(session, meeting_id=m.meeting_id, owner="Alice", status="open")
    for _ in range(4):
        _mk_action(session, meeting_id=m.meeting_id, owner="Bob", status="open")
    _mk_action(session, meeting_id=m.meeting_id, owner="Bob", status="done")

    out = commitments_per_person(session, days=90)
    rows = {p["name"]: p for p in out["persons"]}
    assert rows["Alice"]["assigned"] == 5
    assert rows["Alice"]["completed"] == 3
    assert rows["Alice"]["completion_rate"] == pytest.approx(0.6, abs=1e-3)
    assert rows["Bob"]["assigned"] == 5
    assert rows["Bob"]["completed"] == 1
    assert rows["Bob"]["completion_rate"] == pytest.approx(0.2, abs=1e-3)


def test_overdue_counts_only_open_past_due(session):
    alice = _mk_person(session, "Alice")
    now = datetime.now(timezone.utc)
    m = _mk_meeting(session, date=now - timedelta(days=5))

    past = (now - timedelta(days=2)).strftime("%Y-%m-%d")
    future = (now + timedelta(days=5)).strftime("%Y-%m-%d")

    _mk_action(session, meeting_id=m.meeting_id, owner="Alice", status="open", due_date=past)
    _mk_action(session, meeting_id=m.meeting_id, owner="Alice", status="open", due_date=future)
    _mk_action(session, meeting_id=m.meeting_id, owner="Alice", status="done", due_date=past)

    out = commitments_per_person(session, days=90)
    row = out["persons"][0]
    assert row["overdue"] == 1


def test_sparkline_shape(session):
    _mk_person(session, "Alice")
    now = datetime.now(timezone.utc)
    m = _mk_meeting(session, date=now - timedelta(days=5))
    _mk_action(session, meeting_id=m.meeting_id, owner="Alice", status="done")

    out = commitments_per_person(session, days=90)
    assert len(out["persons"][0]["sparkline"]) == 12


def test_filter_by_meeting_type(session):
    _mk_person(session, "Alice")
    now = datetime.now(timezone.utc)
    m_standup = _mk_meeting(session, date=now - timedelta(days=5), meeting_type="standup")
    m_other = _mk_meeting(session, date=now - timedelta(days=5), meeting_type="team_meeting")
    _mk_action(session, meeting_id=m_standup.meeting_id, owner="Alice")
    _mk_action(session, meeting_id=m_other.meeting_id, owner="Alice")

    filtered = commitments_per_person(session, days=90, meeting_type="standup")
    assert filtered["persons"][0]["assigned"] == 1


def test_empty_when_no_meetings(session):
    _mk_person(session, "Alice")
    out = commitments_per_person(session, days=90)
    assert out["persons"] == []
