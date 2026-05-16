"""MCP tool implementations — thin wrappers around StorageEngine/SearchEngine."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable

from mcp.server import Server
from mcp.types import TextContent, Tool

from meeting_minutes.system3.db import (
    ActionItemORM,
    AnnotationORM,
    DecisionORM,
    MeetingORM,
    MeetingSeriesORM,
)
from meeting_minutes.system3.storage import ActionItemFilters, MeetingFilters


def register(app: Server, db_context: Callable) -> None:
    """Register all MCP tools on the server instance."""

    @app.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="list_meetings",
                description="List meetings in reverse chronological order with optional filters.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "description": "Max results (default 20)", "default": 20},
                        "offset": {"type": "integer", "description": "Pagination offset", "default": 0},
                        "meeting_type": {"type": "string", "description": "Filter by type (standup, one_on_one, team_meeting, etc.)"},
                        "after": {"type": "string", "description": "Only meetings after this date (YYYY-MM-DD)"},
                        "before": {"type": "string", "description": "Only meetings before this date (YYYY-MM-DD)"},
                    },
                },
            ),
            Tool(
                name="get_meeting",
                description="Get full meeting details including minutes, attendees, action items, and decisions.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "meeting_id": {"type": "string", "description": "The meeting ID"},
                    },
                    "required": ["meeting_id"],
                },
            ),
            Tool(
                name="search",
                description=(
                    "Full-text search across meetings, transcripts, and minutes. "
                    "Supports inline filters: type:<meeting_type> after:YYYY-MM-DD before:YYYY-MM-DD"
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query (natural language or with filters)"},
                        "limit": {"type": "integer", "description": "Max results (default 10)", "default": 10},
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="list_action_items",
                description="List action items with optional filters. Shows open/overdue items by default.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "status": {"type": "string", "enum": ["open", "in_progress", "done", "cancelled"], "description": "Filter by status"},
                        "owner": {"type": "string", "description": "Filter by owner name"},
                        "proposal_state": {"type": "string", "enum": ["proposed", "confirmed", "rejected"], "description": "Filter by review state"},
                    },
                },
            ),
            Tool(
                name="update_action_item",
                description="Update an action item's status, owner, or due date.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "action_item_id": {"type": "string", "description": "The action item ID"},
                        "status": {"type": "string", "enum": ["open", "in_progress", "done", "cancelled"]},
                        "owner": {"type": "string", "description": "New owner name (empty string to clear)"},
                        "due_date": {"type": "string", "description": "New due date YYYY-MM-DD (empty string to clear)"},
                    },
                    "required": ["action_item_id"],
                },
            ),
            Tool(
                name="get_transcript",
                description="Get the raw transcript text for a meeting.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "meeting_id": {"type": "string", "description": "The meeting ID"},
                    },
                    "required": ["meeting_id"],
                },
            ),
            Tool(
                name="list_decisions",
                description="List decisions extracted from meetings, optionally filtered by meeting or person.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "meeting_id": {"type": "string", "description": "Filter by meeting"},
                        "made_by": {"type": "string", "description": "Filter by decision-maker name"},
                        "limit": {"type": "integer", "description": "Max results (default 20)", "default": 20},
                    },
                },
            ),
            Tool(
                name="post_note",
                description="Attach a note or annotation to a meeting for later review.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "meeting_id": {"type": "string", "description": "The meeting to annotate"},
                        "content": {"type": "string", "description": "Note content (markdown)"},
                        "label": {
                            "type": "string",
                            "enum": ["question", "blocker", "followup", "observation"],
                            "description": "Optional label for the note",
                        },
                    },
                    "required": ["meeting_id", "content"],
                },
            ),
            Tool(
                name="list_notes",
                description="List annotations/notes for a meeting, optionally filtered by label or resolved state.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "meeting_id": {"type": "string", "description": "Filter by meeting"},
                        "label": {"type": "string", "description": "Filter by label"},
                        "include_resolved": {"type": "boolean", "description": "Include resolved notes (default false)", "default": False},
                    },
                },
            ),
            Tool(
                name="list_series",
                description="List recurring meeting series with cadence and member count.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "description": "Max results (default 20)", "default": 20},
                    },
                },
            ),
        ]

    @app.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        with db_context() as (storage, search, session):
            handler = _HANDLERS.get(name)
            if not handler:
                return [TextContent(type="text", text=f"Unknown tool: {name}")]
            try:
                result = handler(storage, search, session, arguments)
                return [TextContent(type="text", text=result)]
            except Exception as exc:
                return [TextContent(type="text", text=f"Error: {exc}")]

    _HANDLERS: dict[str, Callable] = {
        "list_meetings": _handle_list_meetings,
        "get_meeting": _handle_get_meeting,
        "search": _handle_search,
        "list_action_items": _handle_list_action_items,
        "update_action_item": _handle_update_action_item,
        "get_transcript": _handle_get_transcript,
        "list_decisions": _handle_list_decisions,
        "post_note": _handle_post_note,
        "list_notes": _handle_list_notes,
        "list_series": _handle_list_series,
    }


# ---------------------------------------------------------------------------
# Handler implementations
# ---------------------------------------------------------------------------


def _format_meeting_row(m: MeetingORM) -> str:
    date_str = m.date.strftime("%Y-%m-%d") if m.date else "no date"
    attendees = ", ".join(p.name for p in m.attendees) if m.attendees else "none"
    return (
        f"- **{m.title}** ({m.meeting_type}, {date_str})\n"
        f"  ID: `{m.meeting_id}` | Duration: {m.duration or '?'} | Attendees: {attendees}"
    )


def _handle_list_meetings(storage, search, session, args: dict) -> str:
    filters = MeetingFilters(
        meeting_type=args.get("meeting_type"),
        after_date=datetime.fromisoformat(args["after"]) if args.get("after") else None,
        before_date=datetime.fromisoformat(args["before"]) if args.get("before") else None,
    )
    meetings = storage.list_meetings(
        limit=args.get("limit", 20),
        offset=args.get("offset", 0),
        filters=filters,
    )
    if not meetings:
        return "No meetings found matching the filters."
    lines = [_format_meeting_row(m) for m in meetings]
    return f"Found {len(meetings)} meetings:\n\n" + "\n".join(lines)


def _handle_get_meeting(storage, search, session, args: dict) -> str:
    meeting = storage.get_meeting(args["meeting_id"])
    if not meeting:
        return f"Meeting not found: {args['meeting_id']}"

    parts = []
    date_str = meeting.date.strftime("%Y-%m-%d %H:%M") if meeting.date else "unknown"
    parts.append(f"# {meeting.title}\n")
    parts.append(f"**Type:** {meeting.meeting_type} | **Date:** {date_str} | **Duration:** {meeting.duration or '?'}")
    parts.append(f"**Organizer:** {meeting.organizer or 'unknown'}")
    parts.append(f"**Attendees:** {', '.join(p.name for p in meeting.attendees)}\n")

    if meeting.minutes:
        parts.append(f"## Summary\n{meeting.minutes.summary or 'No summary'}\n")
        if meeting.minutes.markdown_content:
            content = meeting.minutes.markdown_content
            if len(content) > 3000:
                content = content[:3000] + "\n\n... (truncated, use get_transcript for full text)"
            parts.append(f"## Minutes\n{content}\n")

    if meeting.action_items:
        parts.append("## Action Items")
        for ai in meeting.action_items:
            status_icon = {"open": "[ ]", "in_progress": "[~]", "done": "[x]", "cancelled": "[-]"}.get(ai.status, "[ ]")
            owner = f" @{ai.owner}" if ai.owner else ""
            due = f" (due: {ai.due_date})" if ai.due_date else ""
            proposal = f" [{ai.proposal_state}]" if ai.proposal_state != "confirmed" else ""
            parts.append(f"- {status_icon} {ai.description}{owner}{due}{proposal}")
            parts.append(f"  ID: `{ai.action_item_id}`")
        parts.append("")

    if meeting.decisions:
        parts.append("## Decisions")
        for d in meeting.decisions:
            by = f" (by {d.made_by})" if d.made_by else ""
            parts.append(f"- {d.description}{by}")
            if d.rationale:
                parts.append(f"  Rationale: {d.rationale}")
        parts.append("")

    return "\n".join(parts)


def _handle_search(storage, search, session, args: dict) -> str:
    query = search.parse_query(args["query"])
    query.limit = args.get("limit", 10)
    results = search.search(query)

    if not results.results:
        return f"No results for: {args['query']}"

    lines = [f"Found {results.total_count} results (showing {len(results.results)}):\n"]
    for r in results.results:
        date_str = r.date.strftime("%Y-%m-%d") if r.date else "?"
        lines.append(f"- **{r.title}** ({r.meeting_type}, {date_str})")
        lines.append(f"  ID: `{r.meeting_id}`")
        if r.snippet:
            snippet_clean = r.snippet.replace("<b>", "**").replace("</b>", "**")
            lines.append(f"  {snippet_clean}")
    return "\n".join(lines)


def _handle_list_action_items(storage, search, session, args: dict) -> str:
    filters = ActionItemFilters(
        owner=args.get("owner"),
        status=args.get("status"),
        proposal_state=args.get("proposal_state"),
    )
    items = storage.get_action_items(filters=filters)
    if not items:
        return "No action items found."

    lines = [f"Found {len(items)} action items:\n"]
    for ai in items:
        status_icon = {"open": "[ ]", "in_progress": "[~]", "done": "[x]", "cancelled": "[-]"}.get(ai.status, "[ ]")
        owner = f" @{ai.owner}" if ai.owner else ""
        due = f" (due: {ai.due_date})" if ai.due_date else ""
        lines.append(f"- {status_icon} {ai.description}{owner}{due}")
        lines.append(f"  ID: `{ai.action_item_id}` | Meeting: `{ai.meeting_id}` | State: {ai.proposal_state}")
    return "\n".join(lines)


def _handle_update_action_item(storage, search, session, args: dict) -> str:
    action_id = args["action_item_id"]
    item = storage.update_action_item(
        action_id,
        status=args.get("status"),
        owner=args.get("owner"),
        due_date=args.get("due_date"),
    )
    if not item:
        return f"Action item not found: {action_id}"
    return f"Updated action item `{action_id}`: status={item.status}, owner={item.owner}, due={item.due_date}"


def _handle_get_transcript(storage, search, session, args: dict) -> str:
    meeting = storage.get_meeting(args["meeting_id"])
    if not meeting:
        return f"Meeting not found: {args['meeting_id']}"
    if not meeting.transcript:
        return f"No transcript available for meeting: {args['meeting_id']}"
    text = meeting.transcript.full_text or ""
    if len(text) > 8000:
        text = text[:8000] + "\n\n... (truncated at 8000 chars)"
    return f"# Transcript: {meeting.title}\n\n{text}"


def _handle_list_decisions(storage, search, session, args: dict) -> str:
    from sqlalchemy.orm import Session as SASession

    query = session.query(DecisionORM)
    if args.get("meeting_id"):
        query = query.filter(DecisionORM.meeting_id == args["meeting_id"])
    if args.get("made_by"):
        query = query.filter(DecisionORM.made_by == args["made_by"])
    decisions = query.limit(args.get("limit", 20)).all()

    if not decisions:
        return "No decisions found."

    lines = [f"Found {len(decisions)} decisions:\n"]
    for d in decisions:
        by = f" (by {d.made_by})" if d.made_by else ""
        lines.append(f"- {d.description}{by}")
        lines.append(f"  Meeting: `{d.meeting_id}`")
        if d.rationale:
            lines.append(f"  Rationale: {d.rationale}")
    return "\n".join(lines)


def _handle_post_note(storage, search, session, args: dict) -> str:
    meeting = storage.get_meeting(args["meeting_id"])
    if not meeting:
        return f"Meeting not found: {args['meeting_id']}"

    note = AnnotationORM(
        annotation_id=str(uuid.uuid4()),
        meeting_id=args["meeting_id"],
        content=args["content"],
        label=args.get("label"),
        author="mcp",
        created_at=datetime.now(timezone.utc),
        resolved=False,
    )
    session.add(note)
    session.commit()
    label_str = f" [{note.label}]" if note.label else ""
    return f"Note posted{label_str} on meeting `{args['meeting_id']}`: {args['content'][:100]}"


def _handle_list_notes(storage, search, session, args: dict) -> str:
    query = session.query(AnnotationORM)
    if args.get("meeting_id"):
        query = query.filter(AnnotationORM.meeting_id == args["meeting_id"])
    if args.get("label"):
        query = query.filter(AnnotationORM.label == args["label"])
    if not args.get("include_resolved", False):
        query = query.filter(AnnotationORM.resolved == False)  # noqa: E712
    notes = query.order_by(AnnotationORM.created_at.desc()).all()

    if not notes:
        return "No notes found."

    lines = [f"Found {len(notes)} notes:\n"]
    for n in notes:
        label = f" [{n.label}]" if n.label else ""
        resolved = " (resolved)" if n.resolved else ""
        date_str = n.created_at.strftime("%Y-%m-%d %H:%M") if n.created_at else ""
        lines.append(f"- {n.content[:120]}{label}{resolved}")
        lines.append(f"  ID: `{n.annotation_id}` | Meeting: `{n.meeting_id}` | {date_str}")
    return "\n".join(lines)


def _handle_list_series(storage, search, session, args: dict) -> str:
    series_list = session.query(MeetingSeriesORM).limit(args.get("limit", 20)).all()
    if not series_list:
        return "No meeting series found."

    lines = [f"Found {len(series_list)} series:\n"]
    for s in series_list:
        member_count = len(s.members) if s.members else 0
        lines.append(f"- **{s.title}** ({s.meeting_type}, {s.cadence or 'irregular'})")
        lines.append(f"  ID: `{s.series_id}` | Meetings: {member_count}")
    return "\n".join(lines)
