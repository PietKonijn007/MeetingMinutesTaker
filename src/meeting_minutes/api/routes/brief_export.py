"""BRF-2 markdown rendering of a ``BriefingPayload``.

Produces a stable-ordered markdown document suitable for golden-file
testing — the body contains no timestamps, only the header. Empty
sections are omitted.
"""

from __future__ import annotations

from datetime import datetime, timezone

from meeting_minutes.api.routes.brief import (
    BriefFocusFinding,
    BriefingPayload,
    BriefTalkingPoint,
)


def _fmt_attendees(payload: BriefingPayload) -> str:
    return ", ".join(p.name for p in payload.people if p.name) or "(none)"


def _fmt_citations(point: BriefTalkingPoint) -> str:
    if not point.citations:
        return ""
    chunks = []
    for c in point.citations:
        if c.kind == "action":
            chunks.append(f"ACT-{c.ref_id}")
        elif c.kind == "decision":
            chunks.append(f"DEC-{c.ref_id}")
        elif c.kind == "excerpt" and c.meeting_id:
            chunks.append(f"M-{c.meeting_id}")
        elif c.kind == "focus":
            chunks.append(f"FOCUS:{c.ref_id[:30]}")
        elif c.meeting_id:
            chunks.append(f"M-{c.meeting_id}")
    return f"  [→ {', '.join(chunks)}]" if chunks else ""


def _render_focus_finding(finding: BriefFocusFinding) -> list[str]:
    lines = [f"### {finding.focus}", "", finding.answer]
    related = []
    if finding.related_actions:
        related.append("Related actions: " + ", ".join(f"ACT-{a}" for a in finding.related_actions))
    if finding.related_decisions:
        related.append("Related decisions: " + ", ".join(f"DEC-{d}" for d in finding.related_decisions))
    if related:
        lines.append("")
        lines.extend(related)
    lines.append("")
    return lines


def _sentiment_sparkline(scores: list[float]) -> str:
    if not scores:
        return ""
    blocks = "▁▂▃▄▅▆▇█"
    # Normalize sentiment scores (typically -1..1) to 0..1.
    normed = [(max(-1.0, min(1.0, s)) + 1.0) / 2.0 for s in scores]
    return "".join(blocks[min(len(blocks) - 1, int(n * (len(blocks) - 1)))] for n in normed)


def render_markdown(
    payload: BriefingPayload,
    *,
    header_timestamp: datetime | None = None,
) -> str:
    """Render a ``BriefingPayload`` to markdown.

    The body is deterministic for a given payload. ``header_timestamp``
    is the only place where the current time appears — pass ``None`` from
    tests to produce a fully stable output.
    """
    header_dt = header_timestamp or datetime.now(timezone.utc)
    title_topic = payload.topic or (
        payload.who_and_when_last.last_meeting_title
        if payload.who_and_when_last and payload.who_and_when_last.last_meeting_title
        else "Meeting"
    )

    lines: list[str] = [
        f"# Prep Brief — {title_topic}",
        f"_{header_dt.strftime('%Y-%m-%d')} · {_fmt_attendees(payload)}_",
        "",
    ]

    # ---- TL;DR (BRF-1 summary) ----
    if payload.summary:
        lines += ["## TL;DR", "", payload.summary, ""]

    # ---- Suggested talking points ----
    if payload.talking_points:
        lines += ["## Suggested talking points", ""]
        for i, tp in enumerate(payload.talking_points, 1):
            cit = _fmt_citations(tp)
            rationale = f" — {tp.rationale}" if tp.rationale else ""
            prio = f" _(priority: {tp.priority})_" if tp.priority and tp.priority != "medium" else ""
            lines.append(f"{i}. **{tp.text}**{rationale}{cit}{prio}")
        lines.append("")

    # ---- What you asked about ----
    if payload.focus_findings:
        lines += ["## What you asked about", ""]
        for f in payload.focus_findings:
            lines += _render_focus_finding(f)

    # ---- Open commitments ----
    if payload.open_commitments:
        lines += ["## Open commitments", ""]
        for c in payload.open_commitments:
            owner = f" ({c.owner})" if c.owner else ""
            due = f" — due {c.due_date}" if c.due_date else ""
            overdue = " ⚠ overdue" if c.overdue else ""
            lines.append(f"- [ACT-{c.action_id}] {c.description}{owner}{due}{overdue}")
        lines.append("")

    # ---- Unresolved questions ----
    if payload.unresolved_topics:
        lines += ["## Unresolved questions", ""]
        for t in payload.unresolved_topics:
            lines.append(f"- {t.text}")
        lines.append("")

    # ---- Recent decisions ----
    if payload.recent_decisions:
        lines += ["## Recent decisions", ""]
        for d in payload.recent_decisions:
            who = f" — {d.made_by}" if d.made_by else ""
            when = f" ({d.date})" if d.date else ""
            lines.append(f"- **DEC-{d.decision_id}**: {d.description}{who}{when}")
            if d.rationale:
                lines.append(f"  - _Rationale:_ {d.rationale}")
        lines.append("")

    # ---- Recent context ----
    if payload.context_excerpts:
        lines += ["## Recent context", ""]
        for e in payload.context_excerpts:
            heading = f"### {e.date or 'unknown date'} — {e.title or e.meeting_id}"
            lines += [heading, "", (e.chunk_text or "").strip(), ""]

    # ---- Sentiment trend ----
    if payload.recent_sentiment:
        lines += ["## Sentiment trend", ""]
        for person_id, ps in payload.recent_sentiment.items():
            spark = _sentiment_sparkline([p.score for p in ps.scores])
            if spark:
                lines.append(f"- **{ps.name}**: `{spark}`")
        lines.append("")

    # Trim trailing blank lines.
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n"
