"""Tests for ANA-1 Panel 4 — meeting-type effectiveness."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest

from meeting_minutes.stats_analytics import effectiveness_by_type
from meeting_minutes.system3.db import (
    MeetingORM,
    MinutesORM,
    get_session_factory,
)


@pytest.fixture
def session():
    sf = get_session_factory("sqlite:///:memory:")
    s = sf()
    yield s
    s.close()


def _mk(session, meeting_type: str, effectiveness: dict):
    m = MeetingORM(
        meeting_id=f"m-{uuid.uuid4().hex[:8]}",
        title="m",
        date=datetime.now(timezone.utc),
        meeting_type=meeting_type,
        status="final",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session.add(m)
    session.flush()
    session.add(
        MinutesORM(
            meeting_id=m.meeting_id,
            minutes_id=f"min-{uuid.uuid4().hex[:8]}",
            markdown_content="",
            summary="",
            generated_at=datetime.now(timezone.utc),
            llm_model="test",
            structured_json=json.dumps({"meeting_effectiveness": effectiveness}),
        )
    )
    session.commit()


def test_percentage_math(session):
    # 4 standups. 3 had clear agenda, 2 made decisions, 4 had actions,
    # 1 had unresolved items.
    _mk(
        session,
        "standup",
        {
            "had_clear_agenda": True,
            "decisions_made": 1,
            "action_items_assigned": 1,
            "unresolved_items": 0,
        },
    )
    _mk(
        session,
        "standup",
        {
            "had_clear_agenda": True,
            "decisions_made": 0,
            "action_items_assigned": 2,
            "unresolved_items": 1,
        },
    )
    _mk(
        session,
        "standup",
        {
            "had_clear_agenda": False,
            "decisions_made": 2,
            "action_items_assigned": 1,
            "unresolved_items": 0,
        },
    )
    _mk(
        session,
        "standup",
        {
            "had_clear_agenda": True,
            "decisions_made": 0,
            "action_items_assigned": 3,
            "unresolved_items": 0,
        },
    )

    out = effectiveness_by_type(session)
    assert len(out["types"]) == 1
    row = out["types"][0]
    assert row["meeting_count"] == 4
    assert row["had_clear_agenda_pct"] == pytest.approx(0.75, abs=1e-3)
    assert row["decisions_made_pct"] == pytest.approx(0.5, abs=1e-3)
    assert row["action_items_assigned_pct"] == pytest.approx(1.0, abs=1e-3)
    assert row["unresolved_items_pct"] == pytest.approx(0.25, abs=1e-3)


def test_buckets_by_type(session):
    _mk(
        session,
        "standup",
        {"had_clear_agenda": True, "decisions_made": 0, "action_items_assigned": 0, "unresolved_items": 0},
    )
    _mk(
        session,
        "planning",
        {"had_clear_agenda": False, "decisions_made": 1, "action_items_assigned": 2, "unresolved_items": 0},
    )

    out = effectiveness_by_type(session)
    types = {t["type"]: t for t in out["types"]}
    assert types["standup"]["had_clear_agenda_pct"] == 1.0
    assert types["planning"]["decisions_made_pct"] == 1.0


def test_empty_when_no_meetings(session):
    out = effectiveness_by_type(session)
    assert out == {"types": []}
