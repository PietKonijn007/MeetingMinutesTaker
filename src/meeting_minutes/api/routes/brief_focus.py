"""BRF-2 focus-area retrieval and LLM synthesis.

For each user-supplied focus phrase we run an independent retrieval pass
over the attendee set's history:

1. Embedding search over transcript / minutes chunks.
2. Structured scan: action items + decisions filtered by attendee overlap
   and ranked by simple text-match against the focus phrase.
3. LLM synthesis (only when retrieval cleared the ``min_score`` threshold).

If retrieval finds nothing relevant the answer is the literal string
``"No relevant history found."`` and **no LLM call is made** — this keeps
briefs cheap and prevents hallucination on cold starts.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Iterable

from sqlalchemy.orm import Session

from meeting_minutes.api.routes.brief import (
    BriefCitation,
    BriefFocusFinding,
)
from meeting_minutes.config import AppConfig
from meeting_minutes.system3.db import (
    ActionItemORM,
    DecisionORM,
    MeetingORM,
)

logger = logging.getLogger(__name__)


# A literal sentinel — referenced from the spec and from tests. Do not
# parameterize without updating the success criteria.
NO_HISTORY_ANSWER = "No relevant history found."


def _safe_lower(s: str | None) -> str:
    return (s or "").lower()


def _structured_matches(
    session: Session,
    focus: str,
    meeting_ids: list[str],
    attendee_names: list[str],
) -> tuple[list[ActionItemORM], list[DecisionORM]]:
    """Cheap keyword scan against action items + decisions in the meeting set.

    Returns the top few of each, ranked by lowercase substring overlap with
    the focus phrase. This is intentionally simple — the embedding pass does
    the heavier lifting; this just surfaces structured rows for citation.
    """
    if not meeting_ids:
        return [], []

    focus_terms = [t for t in _safe_lower(focus).split() if len(t) > 3]
    if not focus_terms:
        focus_terms = [focus.lower()] if focus else []

    actions = (
        session.query(ActionItemORM)
        .filter(ActionItemORM.meeting_id.in_(meeting_ids))
        .filter(ActionItemORM.proposal_state == "confirmed")
        .all()
    )
    decisions = (
        session.query(DecisionORM)
        .filter(DecisionORM.meeting_id.in_(meeting_ids))
        .all()
    )

    def score(text: str | None) -> int:
        text_l = _safe_lower(text)
        if not text_l or not focus_terms:
            return 0
        return sum(1 for t in focus_terms if t in text_l)

    ranked_actions = sorted(
        actions,
        key=lambda a: (score(a.description), score(a.owner)),
        reverse=True,
    )
    ranked_decisions = sorted(
        decisions,
        key=lambda d: (score(d.description), score(d.rationale)),
        reverse=True,
    )

    top_actions = [a for a in ranked_actions if score(a.description)][:3]
    top_decisions = [d for d in ranked_decisions if score(d.description)][:3]
    return top_actions, top_decisions


def _retrieve_chunks(
    config: AppConfig,
    session: Session,
    focus: str,
    cutoff_iso: str | None,
    meeting_ids: set[str],
    top_k: int,
) -> list[dict]:
    """Embedding search restricted to the attendee meetings."""
    try:
        from meeting_minutes.embeddings import EmbeddingEngine

        engine = EmbeddingEngine(config)
        results = engine.search(
            query=focus,
            session=session,
            limit=top_k * 4,
            after_date=cutoff_iso,
        )
    except Exception as exc:  # pragma: no cover - embedder is optional
        logger.debug("Focus embedding search unavailable: %s", exc)
        return []

    out: list[dict] = []
    for r in results or []:
        mid = r.get("meeting_id")
        if mid and meeting_ids and mid not in meeting_ids:
            continue
        out.append(r)
        if len(out) >= top_k:
            break
    return out


async def _synthesize_focus_answer(
    config: AppConfig,
    focus: str,
    chunks: list[dict],
    actions: list[ActionItemORM],
    decisions: list[DecisionORM],
) -> str:
    """Run a single LLM call to synthesize a 2–4 sentence answer."""
    excerpt_lines = []
    for c in chunks[:6]:
        excerpt = (c.get("text") or "")[:400].replace("\n", " ")
        date = c.get("meeting_date") or ""
        excerpt_lines.append(f"- [{date}] {excerpt}")

    action_lines = [
        f"- ACTION: {a.description} (owner: {a.owner or 'unset'}, status: {a.status})"
        for a in actions
    ]
    decision_lines = [
        f"- DECISION: {d.description} (rationale: {d.rationale or 'n/a'})"
        for d in decisions
    ]

    context_block = "\n".join(excerpt_lines + action_lines + decision_lines) or "(no excerpts)"

    prompt = (
        "You are preparing a meeting brief. The user wants to know about "
        "this specific focus area:\n\n"
        f"FOCUS: {focus}\n\n"
        "Use ONLY the meeting history excerpts below. Write 2–4 short sentences "
        "answering the focus directly. Do not invent facts. If the excerpts do "
        "not actually address the focus, write exactly: "
        f'"{NO_HISTORY_ANSWER}"\n\n'
        f"EXCERPTS:\n{context_block}\n"
    )

    try:
        from meeting_minutes.system2.llm_client import LLMClient

        client = LLMClient(config.generation.llm)
        response = await client.generate(prompt=prompt)
        return (response.text or "").strip() or NO_HISTORY_ANSWER
    except Exception as exc:  # pragma: no cover - network/key failures
        logger.debug("Focus LLM synthesis failed: %s", exc)
        return NO_HISTORY_ANSWER


async def build_focus_finding(
    config: AppConfig,
    session: Session,
    focus: str,
    history_meetings: list[MeetingORM],
    cutoff_iso: str | None,
    attendee_names: list[str],
) -> BriefFocusFinding:
    """Build a single ``BriefFocusFinding`` for one focus phrase."""
    focus = (focus or "").strip()
    if not focus:
        return BriefFocusFinding(focus="", answer=NO_HISTORY_ANSWER)

    meeting_ids = {m.meeting_id for m in history_meetings if m.meeting_id}
    top_k = max(1, int(config.brief.focus.top_k))

    chunks = _retrieve_chunks(
        config=config,
        session=session,
        focus=focus,
        cutoff_iso=cutoff_iso,
        meeting_ids=meeting_ids,
        top_k=top_k,
    )

    # ``distance`` is roughly 1 - cosine_similarity for sqlite-vec; treat
    # similarity = 1 - distance and gate on ``min_score``.
    best_score = 0.0
    for c in chunks:
        d = c.get("distance")
        if d is None:
            continue
        sim = max(0.0, 1.0 - float(d))
        if sim > best_score:
            best_score = sim

    actions, decisions = _structured_matches(
        session=session,
        focus=focus,
        meeting_ids=list(meeting_ids),
        attendee_names=attendee_names,
    )

    citations: list[BriefCitation] = []
    for c in chunks[:3]:
        citations.append(
            BriefCitation(
                kind="excerpt",
                ref_id=str(c.get("chunk_id") or c.get("meeting_id") or ""),
                meeting_id=c.get("meeting_id"),
                snippet=(c.get("text") or "")[:200],
            )
        )
    for a in actions:
        citations.append(
            BriefCitation(
                kind="action",
                ref_id=a.action_item_id,
                meeting_id=a.meeting_id,
                snippet=a.description,
            )
        )
    for d in decisions:
        citations.append(
            BriefCitation(
                kind="decision",
                ref_id=d.decision_id,
                meeting_id=d.meeting_id,
                snippet=d.description,
            )
        )

    related_actions = [a.action_item_id for a in actions]
    related_decisions = [d.decision_id for d in decisions]

    min_score = float(config.brief.focus.min_score)
    has_structured = bool(actions or decisions)

    # Cold-start guard: if neither the embedding pass nor the structured
    # scan produced anything relevant, do NOT call the LLM.
    if best_score < min_score and not has_structured:
        return BriefFocusFinding(
            focus=focus,
            answer=NO_HISTORY_ANSWER,
            citations=[],
            related_actions=[],
            related_decisions=[],
        )

    answer = await _synthesize_focus_answer(
        config=config,
        focus=focus,
        chunks=chunks,
        actions=actions,
        decisions=decisions,
    )

    return BriefFocusFinding(
        focus=focus,
        answer=answer,
        citations=citations,
        related_actions=related_actions,
        related_decisions=related_decisions,
    )


async def build_focus_findings(
    config: AppConfig,
    session: Session,
    focus_items: Iterable[str],
    history_meetings: list[MeetingORM],
    cutoff_iso: str | None,
    attendee_names: list[str],
) -> list[BriefFocusFinding]:
    """Run focus retrieval for each item in parallel."""
    items = [f.strip() for f in (focus_items or []) if (f or "").strip()]
    max_items = int(config.brief.focus.max_items)
    items = items[:max_items]
    if not items:
        return []

    coros = [
        build_focus_finding(
            config=config,
            session=session,
            focus=f,
            history_meetings=history_meetings,
            cutoff_iso=cutoff_iso,
            attendee_names=attendee_names,
        )
        for f in items
    ]
    return list(await asyncio.gather(*coros))
