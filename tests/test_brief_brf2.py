"""Tests for BRF-2 — topic + focus_items + talking_points + export.

These tests ride on the same fixtures as ``test_brief.py`` (in-memory
SQLite + a stubbed embedding engine + a fastapi TestClient) and check
the additive behaviors layered on top of BRF-1.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from meeting_minutes.api.routes.brief import (
    BriefAttendee,
    BriefCitation,
    BriefFocusFinding,
    BriefingPayload,
    BriefOpenCommitment,
    BriefRecentDecision,
    BriefSuggestedStart,
    BriefTalkingPoint,
    BriefWhoAndWhenLast,
    _build_briefing_payload,
)
from meeting_minutes.api.routes.brief_export import render_markdown
from meeting_minutes.api.routes.brief_focus import (
    NO_HISTORY_ANSWER,
    build_focus_finding,
    build_focus_findings,
)
from meeting_minutes.api.routes.brief_talking_points import (
    _build_valid_refs,
    _validate,
    generate_talking_points,
)
from meeting_minutes.config import AppConfig
from meeting_minutes.system3.db import (
    ActionItemORM,
    DecisionORM,
    MeetingORM,
    PersonORM,
    meeting_attendees,
)


# Reuse fixtures from test_brief.py via pytest's normal collection.
from tests.test_brief import (  # noqa: F401  (fixtures registered as side effect)
    session,
    session_factory,
    _mk_action,
    _mk_meeting,
    _mk_person,
    _stub_embedding_engine,
)


# ---------------------------------------------------------------------------
# 1. The cold-start invariant: no embedding hits → no LLM call.
# ---------------------------------------------------------------------------


def test_focus_emits_no_history_sentinel_when_retrieval_is_empty(
    session, monkeypatch
):
    """Spec §7: a focus item with no matching history emits exactly the
    literal ``NO_HISTORY_ANSWER`` and **no LLM call** is made."""
    jon = _mk_person(session, "Jon")
    m = _mk_meeting(
        session,
        title="1:1 with Jon",
        meeting_type="one_on_one",
        date=datetime.now(timezone.utc) - timedelta(days=2),
        attendees=[jon],
    )

    # Stub the embedder to return zero results — simulating "nothing relevant."
    import meeting_minutes.api.routes.brief_focus as bf

    def _empty_search(*_a, **_kw):
        return []

    class _NoHits:
        def __init__(self, *_a, **_kw): ...

        def search(self, *_a, **_kw):
            return []

    import meeting_minutes.embeddings as emb_module

    monkeypatch.setattr(emb_module, "EmbeddingEngine", _NoHits)

    # Trip-wire: if the LLM client gets instantiated for this focus item we
    # fail loudly. The cold-start guard MUST short-circuit before this fires.
    class _Boom:
        def __init__(self, *_a, **_kw):
            raise AssertionError(
                "LLM client must NOT be constructed when focus retrieval finds nothing"
            )

    import meeting_minutes.system2.llm_client as llm_mod

    monkeypatch.setattr(llm_mod, "LLMClient", _Boom)

    cfg = AppConfig()
    finding = asyncio.run(
        build_focus_finding(
            config=cfg,
            session=session,
            focus="Anything about quantum entanglement?",
            history_meetings=[m],
            cutoff_iso=None,
            attendee_names=["Jon"],
        )
    )

    assert finding.answer == NO_HISTORY_ANSWER
    assert finding.citations == []
    assert finding.related_actions == []
    assert finding.related_decisions == []


# ---------------------------------------------------------------------------
# 2. BRF-1 backward compatibility — no topic / no focus → BRF-1 payload.
# ---------------------------------------------------------------------------


def test_brf1_payload_unchanged_when_no_topic_or_focus(session, monkeypatch):
    """Without topic/focus, BRF-2 returns the same shape as BRF-1.

    The only new fields are optional and default to None / [] so existing
    consumers see no change.
    """
    _stub_embedding_engine(monkeypatch)
    jon = _mk_person(session, "Jon")
    _mk_meeting(
        session,
        title="1:1",
        meeting_type="one_on_one",
        date=datetime.now(timezone.utc) - timedelta(days=3),
        attendees=[jon],
    )

    cfg = AppConfig()
    payload = asyncio.run(
        _build_briefing_payload(
            session=session,
            config=cfg,
            person_ids=[jon.person_id],
            meeting_type=None,
            topic=None,
            focus_items=[],
        )
    )

    assert payload.topic is None
    assert payload.focus_items == []
    assert payload.focus_findings == []
    assert payload.talking_points == []


# ---------------------------------------------------------------------------
# 3. Talking-points citation validator drops uncited points.
# ---------------------------------------------------------------------------


def _payload_with_one_action(action_id: str = "a-123") -> BriefingPayload:
    return BriefingPayload(
        people=[BriefAttendee(person_id="p-1", name="Jon")],
        meeting_type="one_on_one",
        who_and_when_last=BriefWhoAndWhenLast(
            attendees=[BriefAttendee(person_id="p-1", name="Jon")],
        ),
        open_commitments=[
            BriefOpenCommitment(
                action_id=action_id,
                owner="Jon",
                description="Send Q3 forecast",
            )
        ],
        unresolved_topics=[],
        recent_sentiment={},
        recent_decisions=[],
        context_excerpts=[],
        suggested_start=BriefSuggestedStart(
            title="1:1 with Jon",
            meeting_type="one_on_one",
            attendee_labels=["Jon"],
            carry_forward_note="",
        ),
    )


def test_validator_drops_talking_points_without_citations():
    """Spec §7: a citation-less response is dropped by the validator."""
    payload = _payload_with_one_action()
    valid_refs = _build_valid_refs(payload)

    # Two raw points: one cited, one not.
    raw = [
        {
            "text": "Confirm the Q3 forecast send-out date.",
            "rationale": "Action is overdue and carried forward.",
            "priority": "high",
            "citations": [
                {"kind": "action", "ref_id": "a-123"},
            ],
        },
        {
            "text": "Discuss progress.",
            "rationale": "",
            "priority": "low",
            "citations": [],  # ← uncited, must be dropped
        },
    ]

    out = _validate(raw, valid_refs, require_citation=True)
    assert len(out) == 1
    assert out[0].text.startswith("Confirm")
    assert out[0].citations[0].kind == "action"
    assert out[0].citations[0].ref_id == "a-123"


def test_validator_drops_invented_ref_ids():
    """Citations pointing at IDs not in the payload are rejected."""
    payload = _payload_with_one_action()
    valid_refs = _build_valid_refs(payload)

    raw = [
        {
            "text": "Ask about the SLA.",
            "rationale": "",
            "priority": "medium",
            "citations": [
                {"kind": "action", "ref_id": "a-DOES-NOT-EXIST"},
            ],
        }
    ]
    out = _validate(raw, valid_refs, require_citation=True)
    assert out == []


def test_talking_points_omitted_when_fewer_than_two_survive(session, monkeypatch):
    """Spec §3.3: 'If fewer than 2 points survive validation, the section
    is omitted rather than padded.'"""
    _stub_embedding_engine(monkeypatch)
    payload = _payload_with_one_action()

    # The LLM returns a single valid point — should still result in [].
    class _OneGoodPoint:
        def __init__(self, *_a, **_kw): ...

        async def generate(self, prompt, system_prompt=""):
            class _R:
                text = json.dumps(
                    {
                        "talking_points": [
                            {
                                "text": "Confirm Q3 forecast.",
                                "rationale": "Action overdue.",
                                "priority": "high",
                                "citations": [
                                    {"kind": "action", "ref_id": "a-123"}
                                ],
                            }
                        ]
                    }
                )

            return _R()

    import meeting_minutes.system2.llm_client as llm_mod

    monkeypatch.setattr(llm_mod, "LLMClient", _OneGoodPoint)

    cfg = AppConfig()
    cfg.brief.summarize_with_llm = True  # → talking_points enabled

    out = asyncio.run(generate_talking_points(cfg, payload))
    assert out == []


# ---------------------------------------------------------------------------
# 4. Markdown export is deterministic except for the header timestamp.
# ---------------------------------------------------------------------------


def test_markdown_export_is_stable_for_same_payload():
    """The body is deterministic; only the header carries a timestamp.

    Render the same payload twice with a fixed header_timestamp — the
    outputs must be byte-identical.
    """
    payload = _payload_with_one_action()
    payload.topic = "Q3 forecast review"
    payload.talking_points = [
        BriefTalkingPoint(
            text="Confirm Q3 forecast.",
            rationale="Action overdue.",
            priority="high",
            citations=[BriefCitation(kind="action", ref_id="a-123")],
        )
    ]
    payload.focus_findings = [
        BriefFocusFinding(
            focus="Outstanding asks",
            answer="Q3 forecast is the only outstanding item.",
            citations=[BriefCitation(kind="action", ref_id="a-123")],
            related_actions=["a-123"],
        )
    ]

    fixed_ts = datetime(2026, 5, 1, 9, 0, 0, tzinfo=timezone.utc)

    md1 = render_markdown(payload, header_timestamp=fixed_ts)
    md2 = render_markdown(payload, header_timestamp=fixed_ts)
    assert md1 == md2

    # And the expected sections appear.
    assert "# Prep Brief — Q3 forecast review" in md1
    assert "## Suggested talking points" in md1
    assert "## What you asked about" in md1
    assert "## Open commitments" in md1
    assert "Confirm Q3 forecast." in md1
    assert "Outstanding asks" in md1


def test_markdown_export_omits_empty_sections():
    payload = _payload_with_one_action()
    # Drop the open commitment; everything else is empty.
    payload.open_commitments = []
    md = render_markdown(payload, header_timestamp=datetime(2026, 5, 1, tzinfo=timezone.utc))
    assert "## Open commitments" not in md
    assert "## What you asked about" not in md
    assert "## Suggested talking points" not in md


# ---------------------------------------------------------------------------
# 5. Cache: re-running an identical brief is fast (no LLM, no embedder).
# ---------------------------------------------------------------------------


def test_cache_round_trip(session, tmp_path):
    """A persisted brief can be loaded back from disk."""
    from meeting_minutes.api.routes.brief_cache import (
        attendee_set_hash,
        find_fresh,
        focus_items_hash,
        load_markdown,
        load_payload,
        topic_hash,
        write,
    )

    cfg = AppConfig()
    cfg.brief.export.output_dir = str(tmp_path / "briefs")

    payload = _payload_with_one_action()
    payload.topic = "Q3 forecast"

    md = render_markdown(payload, header_timestamp=datetime(2026, 5, 1, tzinfo=timezone.utc))
    row = write(
        session=session,
        config=cfg,
        payload=payload,
        person_ids=["p-1"],
        markdown=md,
        model="test-model",
    )

    assert row.id is not None
    assert row.attendee_set_hash == attendee_set_hash(["p-1"])
    assert row.topic_hash == topic_hash("Q3 forecast")
    assert row.focus_items_hash == focus_items_hash([])

    # Re-hydrate from disk.
    reloaded_payload = load_payload(row)
    reloaded_md = load_markdown(row)
    assert reloaded_payload is not None
    assert reloaded_payload.topic == "Q3 forecast"
    assert reloaded_md == md

    # Lookup hits the cache.
    fresh = find_fresh(
        session=session,
        config=cfg,
        person_ids=["p-1"],
        topic="Q3 forecast",
        focus_items=[],
        meeting_type=payload.meeting_type,
    )
    assert fresh is not None
    assert fresh.id == row.id


def test_cache_miss_when_topic_differs(session, tmp_path):
    from meeting_minutes.api.routes.brief_cache import find_fresh, write

    cfg = AppConfig()
    cfg.brief.export.output_dir = str(tmp_path / "briefs")

    payload = _payload_with_one_action()
    payload.topic = "Q3 forecast"
    md = render_markdown(payload, header_timestamp=datetime(2026, 5, 1, tzinfo=timezone.utc))
    write(
        session=session,
        config=cfg,
        payload=payload,
        person_ids=["p-1"],
        markdown=md,
        model="test-model",
    )

    miss = find_fresh(
        session=session,
        config=cfg,
        person_ids=["p-1"],
        topic="Different topic",
        focus_items=[],
        meeting_type=payload.meeting_type,
    )
    assert miss is None
