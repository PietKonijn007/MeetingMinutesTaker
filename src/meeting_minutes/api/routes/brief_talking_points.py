"""BRF-2 talking-points generator.

Given an already-built ``BriefingPayload``, ask the LLM for a short list
of suggested talking points the user should raise. Every emitted point
must cite at least one concrete artifact already present in the payload
(action / decision / open question / excerpt / sentiment / focus). A
post-generation validator drops any point with zero valid citations,
defending against generic "discuss progress" filler.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from meeting_minutes.api.routes.brief import (
    BriefCitation,
    BriefingPayload,
    BriefTalkingPoint,
)
from meeting_minutes.config import AppConfig

logger = logging.getLogger(__name__)


VALID_CITATION_KINDS = {
    "action",
    "decision",
    "open_question",
    "excerpt",
    "sentiment",
    "focus",
}


def _build_valid_refs(payload: BriefingPayload) -> dict[str, set[str]]:
    """Collect the legal ``ref_id`` values for each citation kind."""
    refs: dict[str, set[str]] = {k: set() for k in VALID_CITATION_KINDS}
    for c in payload.open_commitments:
        refs["action"].add(c.action_id)
    for d in payload.recent_decisions:
        refs["decision"].add(d.decision_id)
    for t in payload.unresolved_topics:
        # unresolved topics are keyed by their text in this payload — accept
        # either the raw text or the first meeting id.
        refs["open_question"].add(t.text[:200])
        for mid in t.meeting_ids:
            refs["open_question"].add(mid)
    for e in payload.context_excerpts:
        refs["excerpt"].add(e.meeting_id)
    for person_id in payload.recent_sentiment.keys():
        refs["sentiment"].add(person_id)
    for i, f in enumerate(payload.focus_findings):
        # accept either the index or the focus text
        refs["focus"].add(str(i))
        refs["focus"].add(f.focus[:200])
    return refs


def _build_prompt(payload: BriefingPayload, max_points: int) -> str:
    # Compact JSON so the prompt stays small. Drop verbose excerpts.
    summary: dict[str, Any] = {
        "topic": payload.topic,
        "focus_items": payload.focus_items,
        "attendees": [p.name for p in payload.people],
        "meeting_type": payload.meeting_type,
        "open_commitments": [
            {
                "action_id": c.action_id,
                "owner": c.owner,
                "description": c.description[:200],
                "due_date": c.due_date,
                "overdue": c.overdue,
            }
            for c in payload.open_commitments[:15]
        ],
        "recent_decisions": [
            {
                "decision_id": d.decision_id,
                "description": d.description[:200],
                "date": d.date,
            }
            for d in payload.recent_decisions[:8]
        ],
        "unresolved_topics": [
            {"text": t.text[:200], "meeting_ids": t.meeting_ids[:3]}
            for t in payload.unresolved_topics[:8]
        ],
        "focus_findings": [
            {
                "index": i,
                "focus": f.focus,
                "answer": f.answer[:300],
            }
            for i, f in enumerate(payload.focus_findings)
        ],
        "context_excerpts": [
            {"meeting_id": e.meeting_id, "snippet": (e.chunk_text or "")[:200]}
            for e in payload.context_excerpts[:5]
        ],
    }

    return (
        "You are preparing a meeting brief. Suggest the most useful talking "
        f"points (max {max_points}) the user should raise, given the context "
        "below. EVERY point MUST cite at least one concrete artifact from the "
        "context — use the schema:\n\n"
        '{"talking_points": [{"text": "...", "rationale": "...", '
        '"priority": "high|medium|low", "citations": [{"kind": "action|decision|'
        'open_question|excerpt|sentiment|focus", "ref_id": "...", '
        '"meeting_id": "..."}]}]}\n\n'
        "Rules:\n"
        "- Each ref_id MUST be copied verbatim from the context (action_id, "
        "decision_id, meeting_id, focus index, etc.).\n"
        "- Do NOT invent IDs. If a point cannot be grounded, omit it.\n"
        "- Prefer points that surface overdue commitments, unresolved "
        "questions, or recent decisions the attendees should be aware of.\n"
        "- Output ONLY valid JSON — no prose before or after.\n\n"
        "CONTEXT:\n"
        f"{json.dumps(summary, default=str)}\n"
    )


def _parse_response(raw: str) -> list[dict]:
    """Pull a JSON object out of the model response. Tolerates code fences."""
    if not raw:
        return []
    text = raw.strip()
    # Strip ``` fences if the model added them.
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    # Find the first JSON object.
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        obj = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        logger.debug("Talking-points JSON parse failed: %s", exc)
        return []
    points = obj.get("talking_points", [])
    if not isinstance(points, list):
        return []
    return points


def _validate(
    raw_points: list[dict],
    valid_refs: dict[str, set[str]],
    require_citation: bool,
) -> list[BriefTalkingPoint]:
    out: list[BriefTalkingPoint] = []
    for p in raw_points:
        if not isinstance(p, dict):
            continue
        text = (p.get("text") or "").strip()
        rationale = (p.get("rationale") or "").strip()
        priority = p.get("priority") or "medium"
        if priority not in ("high", "medium", "low"):
            priority = "medium"
        raw_cites = p.get("citations") or []
        if not text:
            continue

        cites: list[BriefCitation] = []
        for c in raw_cites:
            if not isinstance(c, dict):
                continue
            kind = c.get("kind")
            ref_id = str(c.get("ref_id") or "").strip()
            if kind not in VALID_CITATION_KINDS or not ref_id:
                continue
            # Reject IDs that don't appear in the payload.
            if ref_id not in valid_refs.get(kind, set()):
                continue
            cites.append(
                BriefCitation(
                    kind=kind,
                    ref_id=ref_id,
                    meeting_id=c.get("meeting_id"),
                    snippet=(c.get("snippet") or None),
                )
            )

        if require_citation and not cites:
            logger.warning(
                "Dropping uncited talking point: %s",
                text[:80],
            )
            continue

        out.append(
            BriefTalkingPoint(
                text=text,
                rationale=rationale,
                citations=cites,
                priority=priority,
            )
        )
    return out


async def generate_talking_points(
    config: AppConfig,
    payload: BriefingPayload,
) -> list[BriefTalkingPoint]:
    """Run the talking-points LLM call. Returns a possibly-empty list.

    Returns ``[]`` when:
      - generation is disabled,
      - the LLM call fails,
      - parsing fails,
      - or fewer than 2 points survive citation validation (we'd rather
        omit the section than pad it with low-quality output).
    """
    if not config.brief.talking_points_enabled():
        return []

    max_points = int(config.brief.talking_points.max)
    require_citation = bool(config.brief.talking_points.require_citation)
    valid_refs = _build_valid_refs(payload)

    prompt = _build_prompt(payload, max_points)

    try:
        from meeting_minutes.system2.llm_client import LLMClient

        client = LLMClient(config.generation.llm)
        response = await client.generate(prompt=prompt)
        raw = response.text or ""
    except Exception as exc:  # pragma: no cover - network/key failures
        logger.debug("Talking-points LLM call failed: %s", exc)
        return []

    parsed = _parse_response(raw)
    validated = _validate(parsed, valid_refs, require_citation)

    if len(validated) < 2:
        # "If fewer than 2 points survive validation, the section is omitted
        # rather than padded." (spec §3.3 / §3.2)
        if validated:
            logger.info(
                "Talking points: only %d survived validation — omitting section.",
                len(validated),
            )
        return []

    return validated[:max_points]
