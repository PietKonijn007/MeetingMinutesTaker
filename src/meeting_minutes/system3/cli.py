"""Typer CLI interface for Meeting Minutes Taker."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from meeting_minutes.config import ConfigLoader
from meeting_minutes.env import load_dotenv

# Load .env before anything else
load_dotenv()

app = typer.Typer(
    name="mm",
    help="Meeting Minutes Taker — record, transcribe, and search meeting minutes.",
    no_args_is_help=True,
)

record_app = typer.Typer(help="Recording commands.")
actions_app = typer.Typer(help="Action item commands.")

app.add_typer(record_app, name="record")
app.add_typer(actions_app, name="actions")

console = Console()
err_console = Console(stderr=True)


def _load_config():
    return ConfigLoader.load_default()


def _get_db_session(config=None):
    """Create a database session."""
    from meeting_minutes.system3.db import get_session_factory

    if config is None:
        config = _load_config()

    db_path = Path(config.storage.sqlite_path).expanduser()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    session_factory = get_session_factory(f"sqlite:///{db_path}")
    return session_factory()


def _get_storage_and_search(config=None):
    from meeting_minutes.system3.search import SearchEngine
    from meeting_minutes.system3.storage import StorageEngine

    session = _get_db_session(config)
    storage = StorageEngine(session)
    search = SearchEngine(session)
    return storage, search


# ---------------------------------------------------------------------------
# mm search
# ---------------------------------------------------------------------------


@app.command("search")
def search_cmd(
    query: str = typer.Argument(..., help="Search query"),
    type_filter: Optional[str] = typer.Option(None, "--type", "-t", help="Filter by meeting type"),
    after: Optional[str] = typer.Option(None, "--after", "-a", help="After date (YYYY-MM-DD)"),
    before: Optional[str] = typer.Option(None, "--before", "-b", help="Before date (YYYY-MM-DD)"),
    limit: int = typer.Option(20, "--limit", "-n", help="Max results"),
):
    """Search meeting minutes."""
    from meeting_minutes.models import SearchQuery
    from meeting_minutes.system3.search import SearchEngine
    from meeting_minutes.system3.storage import StorageEngine

    storage, search = _get_storage_and_search()
    parsed = search.parse_query(query)

    if type_filter:
        parsed.meeting_type = type_filter
    if after:
        try:
            parsed.after_date = datetime.fromisoformat(after)
        except ValueError:
            err_console.print(f"[red]Invalid date format for --after: {after}[/red]")
            raise typer.Exit(code=1)
    if before:
        try:
            parsed.before_date = datetime.fromisoformat(before)
        except ValueError:
            err_console.print(f"[red]Invalid date format for --before: {before}[/red]")
            raise typer.Exit(code=1)

    parsed.limit = limit
    results = search.search(parsed)

    if not results.results:
        console.print("[yellow]No results found.[/yellow]")
        return

    table = Table(title=f"Search Results ({results.total_count} total)")
    table.add_column("Meeting ID", style="dim", width=12)
    table.add_column("Title")
    table.add_column("Date")
    table.add_column("Type")
    table.add_column("Snippet")

    for r in results.results:
        table.add_row(
            r.meeting_id[:8] + "...",
            r.title,
            r.date.strftime("%Y-%m-%d"),
            r.meeting_type,
            r.snippet[:60] if r.snippet else "",
        )

    console.print(table)


# ---------------------------------------------------------------------------
# mm list
# ---------------------------------------------------------------------------


@app.command("list")
def list_cmd(
    person: Optional[str] = typer.Option(None, "--person", help="Filter by attendee email"),
    limit: int = typer.Option(20, "--limit", "-n"),
):
    """List meetings in reverse chronological order."""
    from meeting_minutes.system3.storage import MeetingFilters

    storage, _ = _get_storage_and_search()
    meetings = storage.list_meetings(limit=limit)

    if not meetings:
        console.print("[yellow]No meetings found.[/yellow]")
        return

    table = Table(title="Meetings")
    table.add_column("Meeting ID", style="dim", width=38)
    table.add_column("Title")
    table.add_column("Date")
    table.add_column("Type")
    table.add_column("Duration")

    for m in meetings:
        date_str = m.date.strftime("%Y-%m-%d") if m.date else "unknown"
        table.add_row(
            m.meeting_id,
            m.title or "",
            date_str,
            m.meeting_type or "",
            m.duration or "",
        )

    console.print(table)


# ---------------------------------------------------------------------------
# mm show
# ---------------------------------------------------------------------------


@app.command("show")
def show_cmd(
    meeting_id: str = typer.Argument(..., help="Meeting ID"),
):
    """Show meeting details."""
    storage, _ = _get_storage_and_search()
    meeting = storage.get_meeting(meeting_id)

    if meeting is None:
        err_console.print(f"[red]Meeting not found: {meeting_id}[/red]")
        raise typer.Exit(code=1)

    date_str = meeting.date.strftime("%Y-%m-%d %H:%M") if meeting.date else "unknown"

    content_lines = [
        f"**ID:** {meeting.meeting_id}",
        f"**Title:** {meeting.title}",
        f"**Date:** {date_str}",
        f"**Type:** {meeting.meeting_type}",
        f"**Duration:** {meeting.duration}",
    ]

    if meeting.organizer:
        content_lines.append(f"**Organizer:** {meeting.organizer}")

    attendee_names = [a.name for a in meeting.attendees]
    if attendee_names:
        content_lines.append(f"**Attendees:** {', '.join(attendee_names)}")

    if meeting.minutes and meeting.minutes.summary:
        content_lines.append("")
        content_lines.append("**Summary:**")
        content_lines.append(meeting.minutes.summary[:500])

    if meeting.action_items:
        content_lines.append("")
        content_lines.append(f"**Action Items:** {len(meeting.action_items)}")
        for ai in meeting.action_items[:5]:
            status_icon = "✓" if ai.status == "done" else "○"
            content_lines.append(f"  {status_icon} {ai.description[:80]}")

    console.print(
        Panel(
            "\n".join(content_lines),
            title=f"Meeting: {meeting.title}",
            border_style="blue",
        )
    )


# ---------------------------------------------------------------------------
# mm actions
# ---------------------------------------------------------------------------


@actions_app.callback(invoke_without_command=True)
def actions_cmd(
    ctx: typer.Context,
    owner: Optional[str] = typer.Option(None, "--owner", help="Filter by owner email"),
    overdue: bool = typer.Option(False, "--overdue", help="Show only overdue items"),
    status: Optional[str] = typer.Option(None, "--status", help="Filter by status"),
):
    """List action items."""
    if ctx.invoked_subcommand is not None:
        return

    from meeting_minutes.system3.storage import ActionItemFilters

    storage, _ = _get_storage_and_search()
    filters = ActionItemFilters(owner=owner, status=status or "open", overdue=overdue)
    items = storage.get_action_items(filters)

    if not items:
        console.print("[yellow]No action items found.[/yellow]")
        return

    table = Table(title="Action Items")
    table.add_column("ID", style="dim", width=10)
    table.add_column("Description")
    table.add_column("Owner")
    table.add_column("Due Date")
    table.add_column("Status")
    table.add_column("Meeting ID", style="dim")

    for item in items:
        table.add_row(
            item.action_item_id[:8],
            item.description[:80] if item.description else "",
            item.owner or "",
            item.due_date or "",
            item.status or "open",
            item.meeting_id[:8] + "..." if item.meeting_id else "",
        )

    console.print(table)


@actions_app.command("complete")
def actions_complete_cmd(
    action_id: str = typer.Argument(..., help="Action item ID"),
):
    """Mark action item as done."""
    storage, _ = _get_storage_and_search()
    ok = storage.update_action_item_status(action_id, "done")

    if not ok:
        err_console.print(f"[red]Action item not found: {action_id}[/red]")
        raise typer.Exit(code=1)

    console.print(f"[green]Action item {action_id} marked as done.[/green]")


# ---------------------------------------------------------------------------
# mm delete
# ---------------------------------------------------------------------------


@app.command("delete")
def delete_cmd(
    meeting_id: str = typer.Argument(..., help="Meeting ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Delete a meeting and all associated data."""
    if not yes:
        confirmed = typer.confirm(f"Delete meeting {meeting_id}? This cannot be undone.")
        if not confirmed:
            console.print("Aborted.")
            return

    storage, search = _get_storage_and_search()
    search.remove_from_index(meeting_id)
    ok = storage.delete_meeting(meeting_id)

    if not ok:
        err_console.print(f"[red]Meeting not found: {meeting_id}[/red]")
        raise typer.Exit(code=1)

    console.print(f"[green]Meeting {meeting_id} deleted.[/green]")


# ---------------------------------------------------------------------------
# mm record start/stop
# ---------------------------------------------------------------------------


@record_app.command("start")
def record_start_cmd():
    """Start recording a meeting."""
    from meeting_minutes.config import ConfigLoader
    from meeting_minutes.system1.capture import AudioCaptureEngine

    config = _load_config()
    engine = AudioCaptureEngine(config.recording)

    try:
        meeting_id = engine.start()
        console.print(f"[green]Recording started. Meeting ID: {meeting_id}[/green]")
        console.print("Run [bold]mm record stop[/bold] to stop recording.")

        # Save state for stop command
        state_file = Path("/tmp/mm_recording_state.json")
        state_file.write_text(json.dumps({"meeting_id": meeting_id}))
    except Exception as exc:
        err_console.print(f"[red]Failed to start recording: {exc}[/red]")
        raise typer.Exit(code=1)


@record_app.command("stop")
def record_stop_cmd():
    """Stop recording and transcribe."""
    state_file = Path("/tmp/mm_recording_state.json")
    if not state_file.exists():
        err_console.print("[red]No active recording found.[/red]")
        raise typer.Exit(code=1)

    state = json.loads(state_file.read_text())
    meeting_id = state.get("meeting_id")

    console.print(f"[yellow]Stopping recording for meeting {meeting_id}...[/yellow]")
    console.print("[dim]Transcription would run here in full mode.[/dim]")
    state_file.unlink(missing_ok=True)
    console.print(f"[green]Recording stopped. Meeting ID: {meeting_id}[/green]")


# ---------------------------------------------------------------------------
# mm generate
# ---------------------------------------------------------------------------


@app.command("generate")
def generate_cmd(
    meeting_id: str = typer.Argument(..., help="Meeting ID"),
):
    """Generate minutes for a meeting from its transcript."""
    config = _load_config()

    async def _run():
        from meeting_minutes.pipeline import PipelineOrchestrator

        orchestrator = PipelineOrchestrator(config)
        try:
            path = await orchestrator.run_generation(meeting_id)
            console.print(f"[green]Minutes generated: {path}[/green]")
        except Exception as exc:
            err_console.print(f"[red]Generation failed: {exc}[/red]")
            raise typer.Exit(code=1)

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# mm reprocess
# ---------------------------------------------------------------------------


@app.command("reprocess")
def reprocess_cmd(
    meeting_id: str = typer.Argument(..., help="Meeting ID"),
):
    """Reprocess a meeting through the full pipeline."""
    config = _load_config()

    async def _run():
        from meeting_minutes.pipeline import PipelineOrchestrator

        orchestrator = PipelineOrchestrator(config)
        try:
            await orchestrator.reprocess(meeting_id)
            console.print(f"[green]Meeting {meeting_id} reprocessed.[/green]")
        except Exception as exc:
            err_console.print(f"[red]Reprocessing failed: {exc}[/red]")
            raise typer.Exit(code=1)

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# mm init
# ---------------------------------------------------------------------------


@app.command("init")
def init_cmd():
    """Initialize the database and data directories."""
    config = _load_config()

    # Create data directories
    data_dir = Path(config.data_dir).expanduser()
    for subdir in ["recordings", "transcripts", "minutes", "exports"]:
        (data_dir / subdir).mkdir(parents=True, exist_ok=True)

    # Create db directory and initialize tables
    db_path = Path(config.storage.sqlite_path).expanduser()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    from meeting_minutes.system3.db import get_session_factory

    get_session_factory(f"sqlite:///{db_path}")

    # Create logs directory
    Path("logs").mkdir(exist_ok=True)

    console.print(f"[green]Initialized data directories at {data_dir}[/green]")
    console.print(f"[green]Database created at {db_path}[/green]")
    console.print("[dim]Run 'mm serve' to start the web UI, or 'mm record start' to record a meeting.[/dim]")


# ---------------------------------------------------------------------------
# mm serve
# ---------------------------------------------------------------------------


@app.command("serve")
def serve_cmd(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address"),
    port: int = typer.Option(8080, "--port", "-p", help="Port number"),
):
    """Start the API server."""
    import uvicorn

    console.print(f"[green]Starting API server on {host}:{port}[/green]")
    uvicorn.run(
        "meeting_minutes.api.main:app",
        host=host,
        port=port,
        reload=False,
    )


if __name__ == "__main__":
    app()
