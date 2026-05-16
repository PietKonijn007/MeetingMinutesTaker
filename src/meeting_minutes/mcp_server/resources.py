"""MCP resources — read-only context for AI agents."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from mcp.server import Server
from mcp.types import Resource, TextResourceContents

from meeting_minutes.system3.db import ActionItemORM
from meeting_minutes.system3.storage import ActionItemFilters


def register(app: Server, db_context: Callable) -> None:
    """Register MCP resources on the server."""

    @app.list_resources()
    async def list_resources() -> list[Resource]:
        return [
            Resource(
                uri="meetings://recent",
                name="Recent Meetings",
                description="Last 5 meetings with titles, dates, and types",
                mimeType="text/plain",
            ),
            Resource(
                uri="meetings://actions/open",
                name="Open Action Items",
                description="All confirmed open action items across meetings",
                mimeType="text/plain",
            ),
        ]

    @app.read_resource()
    async def read_resource(uri: str) -> str:
        with db_context() as (storage, search, session):
            if uri == "meetings://recent":
                meetings = storage.list_meetings(limit=5)
                if not meetings:
                    return "No meetings yet."
                lines = []
                for m in meetings:
                    date_str = m.date.strftime("%Y-%m-%d") if m.date else "?"
                    attendees = ", ".join(p.name for p in m.attendees) if m.attendees else "none"
                    lines.append(f"- {m.title} ({m.meeting_type}, {date_str}) — {attendees}")
                    lines.append(f"  ID: {m.meeting_id}")
                return "\n".join(lines)

            elif uri == "meetings://actions/open":
                filters = ActionItemFilters(status="open", proposal_state="confirmed")
                items = storage.get_action_items(filters=filters)
                if not items:
                    return "No open action items."
                lines = []
                for ai in items:
                    owner = f" @{ai.owner}" if ai.owner else ""
                    due = f" (due: {ai.due_date})" if ai.due_date else ""
                    lines.append(f"- {ai.description}{owner}{due}")
                    lines.append(f"  ID: {ai.action_item_id} | Meeting: {ai.meeting_id}")
                return "\n".join(lines)

            return f"Unknown resource: {uri}"
