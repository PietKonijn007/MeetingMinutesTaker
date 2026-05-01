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

from meeting_minutes.config import ConfigLoader, resolve_db_path
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
backup_app = typer.Typer(help="Database backup commands.")
service_app = typer.Typer(help="Manage the auto-start service (macOS).")
series_app = typer.Typer(help="Recurring-meeting series (REC-1).")
stats_app = typer.Typer(help="Analytics maintenance (ANA-1).")

app.add_typer(record_app, name="record")
app.add_typer(actions_app, name="actions")
app.add_typer(backup_app, name="backup")
app.add_typer(service_app, name="service")
app.add_typer(series_app, name="series")
app.add_typer(stats_app, name="stats")

console = Console()
err_console = Console(stderr=True)


def _load_config():
    return ConfigLoader.load_default()


def _get_db_session(config=None):
    """Create a database session."""
    from meeting_minutes.system3.db import get_session_factory

    if config is None:
        config = _load_config()

    db_path = resolve_db_path(config.storage.sqlite_path)
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
    proposed: bool = typer.Option(
        False,
        "--proposed",
        help="Show proposed actions awaiting review instead of confirmed ones",
    ),
    all_states: bool = typer.Option(
        False,
        "--all-states",
        help="Show actions in every proposal state (confirmed + proposed + rejected)",
    ),
):
    """List action items.

    Defaults to confirmed-only (matches the global tracker UI). Use ``--proposed``
    to triage proposals from the CLI, or ``--all-states`` to see everything.
    """
    if ctx.invoked_subcommand is not None:
        return

    from meeting_minutes.system3.storage import ActionItemFilters

    storage, _ = _get_storage_and_search()
    if all_states:
        ps_filter: Optional[str] = None
    elif proposed:
        ps_filter = "proposed"
    else:
        ps_filter = "confirmed"
    filters = ActionItemFilters(
        owner=owner,
        status=status or "open",
        overdue=overdue,
        proposal_state=ps_filter,
    )
    items = storage.get_action_items(filters)

    if not items:
        console.print("[yellow]No action items found.[/yellow]")
        return

    title = "Action Items"
    if ps_filter == "proposed":
        title = "Action Items — Proposed (awaiting review)"
    elif ps_filter is None:
        title = "Action Items — All states"
    table = Table(title=title)
    table.add_column("ID", style="dim", width=10)
    table.add_column("Description")
    table.add_column("Owner")
    table.add_column("Due Date")
    table.add_column("Status")
    table.add_column("Review")
    table.add_column("Meeting ID", style="dim")

    for item in items:
        table.add_row(
            item.action_item_id[:8],
            item.description[:80] if item.description else "",
            item.owner or "",
            item.due_date or "",
            item.status or "open",
            item.proposal_state or "proposed",
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
def record_start_cmd(
    planned_minutes: Optional[int] = typer.Option(None, "--planned-minutes", help="Expected recording length for disk preflight."),
    force: bool = typer.Option(False, "--force", help="Ignore red-tier preflight refusal."),
):
    """Start recording a meeting."""
    from meeting_minutes.config import ConfigLoader
    from meeting_minutes.system1.capture import AudioCaptureEngine, preflight_disk_check

    config = _load_config()

    # DSK-1 preflight. Interactive users see warnings; non-interactive
    # mode (e.g. launchd) refuses red-tier starts so we don't fill the
    # disk mid-meeting with no human around.
    preflight = preflight_disk_check(config, planned_minutes=planned_minutes)
    is_interactive = sys.stdin.isatty() and sys.stdout.isatty()
    if preflight.tier == "red":
        if not is_interactive and not force:
            err_console.print(
                f"[red]Disk preflight RED — refusing to start (non-interactive). "
                f"free={preflight.free_bytes} estimated={preflight.estimated_bytes}. "
                f"Re-run interactively or with --force.[/red]"
            )
            raise typer.Exit(code=1)
        console.print(f"[red]{preflight.message}[/red] free={preflight.free_bytes}B estimated={preflight.estimated_bytes}B")
    elif preflight.tier in ("yellow", "orange"):
        console.print(f"[yellow]{preflight.message}[/yellow] free={preflight.free_bytes}B estimated={preflight.estimated_bytes}B")

    engine = AudioCaptureEngine(
        config.recording,
        app_config=config,
        planned_minutes=planned_minutes,
    )

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
# mm brief — BRF-2 prep brief
# ---------------------------------------------------------------------------


def _resolve_attendees_to_person_ids(session, names_or_emails: list[str]) -> tuple[list[str], list[str]]:
    """Match attendee tokens against PersonORM.name and .email.

    Returns ``(matched_person_ids, unmatched_tokens)`` preserving caller order.
    Tokens are matched case-insensitively against email (exact) then name
    (exact, then case-insensitive).
    """
    from meeting_minutes.system3.db import PersonORM

    if not names_or_emails:
        return [], []

    all_people = session.query(PersonORM).all()
    by_email = {(p.email or "").lower(): p for p in all_people if p.email}
    by_name = {(p.name or "").lower(): p for p in all_people if p.name}

    matched: list[str] = []
    unmatched: list[str] = []
    for token in names_or_emails:
        t = (token or "").strip()
        if not t:
            continue
        person = by_email.get(t.lower()) or by_name.get(t.lower())
        if person is None:
            unmatched.append(t)
        else:
            matched.append(person.person_id)
    return matched, unmatched


@app.command("brief")
def brief_cmd(
    attendees: str = typer.Option(
        ...,
        "--attendees", "-a",
        help="Comma-separated names or emails (e.g. 'Jon Porter,sarah@acme.com').",
    ),
    topic: str = typer.Option(
        ...,
        "--topic", "-t",
        help="What the meeting is about. Drives topic-RAG retrieval.",
    ),
    focus: list[str] = typer.Option(
        [],
        "--focus", "-f",
        help="A specific thing to look for. Repeat for multiple items.",
    ),
    type: Optional[str] = typer.Option(
        None, "--type", help="Optional meeting-type hint (vendor_call, etc.)",
    ),
    format: str = typer.Option(
        "md", "--format", help="Output format: md | json",
    ),
    out: Optional[Path] = typer.Option(
        None, "--out", "-o",
        help="File to write to. Omit to write a default-named file under data/briefs/.",
    ),
):
    """Generate a pre-meeting brief (BRF-2)."""
    if format not in ("md", "json"):
        err_console.print("[red]--format must be 'md' or 'json'[/red]")
        raise typer.Exit(code=2)

    config = _load_config()
    session = _get_db_session(config)

    tokens = [t.strip() for t in (attendees or "").split(",") if t.strip()]
    person_ids, unmatched = _resolve_attendees_to_person_ids(session, tokens)
    for u in unmatched:
        err_console.print(
            f"[yellow]warning:[/yellow] no person found for '{u}' — skipping. "
            "Create the person first or fix the spelling."
        )
    if not person_ids:
        err_console.print("[red]No matching attendees. Aborting.[/red]")
        raise typer.Exit(code=2)

    async def _run():
        from meeting_minutes.api.routes.brief import _build_briefing_payload
        from meeting_minutes.api.routes.brief_export import render_markdown
        from meeting_minutes.api.routes.brief_cache import write as cache_write

        payload = await _build_briefing_payload(
            session=session,
            config=config,
            person_ids=person_ids,
            meeting_type=type,
            topic=topic,
            focus_items=list(focus or []),
        )
        markdown = render_markdown(payload)

        # Write to data/briefs/ via cache module, plus optional --out copy.
        row = cache_write(
            session=session,
            config=config,
            payload=payload,
            person_ids=person_ids,
            markdown=markdown,
            model=config.generation.llm.model,
        )

        if out is not None:
            out.parent.mkdir(parents=True, exist_ok=True)
            if format == "md":
                out.write_text(markdown, encoding="utf-8")
            else:
                out.write_text(payload.model_dump_json(indent=2), encoding="utf-8")
            console.print(f"[green]Brief written to {out}[/green]")
        else:
            if format == "md":
                console.print(f"[green]Brief written to {row.markdown_path}[/green]")
            else:
                console.print(f"[green]Brief written to {row.json_path}[/green]")

    try:
        asyncio.run(_run())
    except SystemExit:
        raise
    except Exception as exc:
        err_console.print(f"[red]Brief generation failed: {exc}[/red]")
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# mm export — EXP-1 (per-meeting or bulk series)
# ---------------------------------------------------------------------------


@app.command("export")
def export_cmd(
    meeting_id: Optional[str] = typer.Argument(
        None,
        help="Meeting ID to export (omit when using --series)",
    ),
    format: str = typer.Option("pdf", "--format", "-f", help="pdf | docx | md"),
    with_transcript: bool = typer.Option(
        False, "--with-transcript", help="Append the full transcript",
    ),
    out: Optional[Path] = typer.Option(
        None, "--out", "-o",
        help="Output file (single) or directory (--series). Defaults under data/exports/.",
    ),
    series: Optional[str] = typer.Option(
        None, "--series", help="Export every meeting in this series as a ZIP bundle",
    ),
):
    """Export a meeting (or a whole series) to PDF / DOCX / Markdown."""
    from meeting_minutes.export import (
        ExportDependencyMissing,
        default_filename,
        export as render_export,
        slugify,
    )
    from meeting_minutes.export.bundle import make_zip
    from meeting_minutes.system3.db import (
        MeetingORM,
        MeetingSeriesMemberORM,
        MeetingSeriesORM,
    )

    if format not in ("pdf", "docx", "md"):
        err_console.print("[red]--format must be one of: pdf, docx, md[/red]")
        raise typer.Exit(code=2)
    if series is None and meeting_id is None:
        err_console.print("[red]Provide either a meeting_id or --series=<id>[/red]")
        raise typer.Exit(code=2)
    if series is not None and meeting_id is not None:
        err_console.print("[red]Pass a meeting_id or --series, not both[/red]")
        raise typer.Exit(code=2)

    config = _load_config()
    session = _get_db_session(config)

    # Default output directory under the project data dir.
    default_dir = Path(config.data_dir).expanduser() / "exports"

    try:
        if meeting_id is not None:
            m = session.get(MeetingORM, meeting_id)
            if m is None:
                err_console.print(f"[red]No meeting with ID {meeting_id}[/red]")
                raise typer.Exit(code=1)
            try:
                result = render_export(m, format=format, with_transcript=with_transcript)
            except ExportDependencyMissing as exc:
                err_console.print(f"[red]{exc}[/red]")
                raise typer.Exit(code=1)

            out_path = out
            if out_path is None:
                default_dir.mkdir(parents=True, exist_ok=True)
                out_path = default_dir / result.filename
            elif out_path.is_dir():
                out_path = out_path / result.filename
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(result.content)
            console.print(f"[green]Exported:[/green] {out_path}")
            return

        # Bulk series export.
        series_row = session.get(MeetingSeriesORM, series)
        if series_row is None:
            err_console.print(f"[red]No series with ID {series}[/red]")
            raise typer.Exit(code=1)

        member_ids = [
            r.meeting_id
            for r in session.query(MeetingSeriesMemberORM)
            .filter_by(series_id=series)
            .all()
        ]
        members = (
            session.query(MeetingORM)
            .filter(MeetingORM.meeting_id.in_(member_ids))
            .order_by(MeetingORM.date.asc())
            .all()
        )
        if not members:
            err_console.print(f"[red]Series {series} has no meetings[/red]")
            raise typer.Exit(code=1)

        results = []
        for m in members:
            if m.minutes is None or not (m.minutes.markdown_content or "").strip():
                console.print(f"[yellow]Skipping {m.meeting_id}: no minutes yet[/yellow]")
                continue
            try:
                results.append(
                    render_export(m, format=format, with_transcript=with_transcript)
                )
            except ExportDependencyMissing as exc:
                err_console.print(f"[red]{exc}[/red]")
                raise typer.Exit(code=1)
        if not results:
            err_console.print("[red]No member meetings had minutes to export[/red]")
            raise typer.Exit(code=1)

        zip_bytes = make_zip(results)
        zip_name = f"{slugify(series_row.title or series)}.zip"
        if out is None:
            default_dir.mkdir(parents=True, exist_ok=True)
            out_path = default_dir / zip_name
        elif out.is_dir() or not out.suffix:
            out.mkdir(parents=True, exist_ok=True)
            out_path = out / zip_name
        else:
            out_path = out
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(zip_bytes)
        console.print(
            f"[green]Exported {len(results)} meetings →[/green] {out_path}"
        )
    finally:
        session.close()


# ---------------------------------------------------------------------------
# mm reprocess
# ---------------------------------------------------------------------------


@app.command("reprocess")
def reprocess_cmd(
    meeting_id: str = typer.Argument(..., help="Meeting ID"),
):
    """Reprocess a meeting through generation + ingestion (skips transcription/diarization)."""
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
# mm rediarize
# ---------------------------------------------------------------------------


@app.command("rediarize")
def rediarize_cmd(
    meeting_id: str = typer.Argument(..., help="Meeting ID"),
    skip_regenerate: bool = typer.Option(False, "--skip-regenerate", help="Only re-diarize, don't re-run generation/ingestion"),
    num_speakers: Optional[int] = typer.Option(None, "--num-speakers", "-n", help="Exact speaker count (constrains clustering — biggest accuracy + speed win)"),
    min_speakers: Optional[int] = typer.Option(None, "--min-speakers", help="Lower bound on speaker count"),
    max_speakers: Optional[int] = typer.Option(None, "--max-speakers", help="Upper bound on speaker count"),
):
    """Re-run speaker diarization on existing audio without re-transcribing.

    Useful when diarization failed at recording time (e.g. missing HF_TOKEN
    or torchcodec) and you want to add speaker labels to an existing
    meeting without paying the cost of re-transcription.

    By default, also re-runs minutes generation and DB ingestion so the
    new speaker labels appear everywhere. Use --skip-regenerate to only
    update the transcript JSON.

    --num-speakers/--min-speakers/--max-speakers override any speaker hint
    from the meeting's notes sidecar. Use --num-speakers when you know the
    exact count (e.g. you've watched the transcript) — this is by far the
    biggest single quality and runtime win for noisy diarization.
    """
    config = _load_config()
    # Up-front diagnostic so a user can see at a glance which backend will
    # run — saves an hour of debugging when the rediarize is hitting the
    # local PyTorch path and they expected the cloud one.
    console.print(
        f"  [dim]Diarization backend: {config.diarization.engine} "
        f"(model={config.diarization.model})[/dim]"
    )
    if config.diarization.engine == "pyannote-ai":
        console.print(
            f"  [dim]Tier: {config.diarization.pyannote_ai.tier} | "
            f"key from ${config.diarization.pyannote_ai.api_key_env}[/dim]"
        )

    async def _run():
        from meeting_minutes.pipeline import PipelineOrchestrator

        orchestrator = PipelineOrchestrator(config)
        try:
            await orchestrator.rediarize(
                meeting_id,
                regenerate=not skip_regenerate,
                num_speakers=num_speakers,
                min_speakers=min_speakers,
                max_speakers=max_speakers,
            )
            console.print(f"[green]Meeting {meeting_id} re-diarized.[/green]")
        except Exception as exc:
            err_console.print(f"[red]Re-diarize failed: {exc}[/red]")
            raise typer.Exit(code=1)

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# mm status (pipeline state) + mm resume
# ---------------------------------------------------------------------------


_STATUS_STYLE = {
    "pending": "dim",
    "running": "yellow",
    "succeeded": "green",
    "failed": "red",
    "skipped": "blue",
}


@app.command("status")
def status_cmd(
    meeting_id: str = typer.Argument(..., help="Meeting ID"),
):
    """Show per-stage pipeline state for a meeting."""
    from meeting_minutes.pipeline_state import Stage, get_stages

    session = _get_db_session()
    try:
        states = get_stages(session, meeting_id)
    finally:
        session.close()

    if not states:
        console.print(f"[yellow]No pipeline state recorded for {meeting_id}[/yellow]")
        return

    table = Table(title=f"Pipeline state — {meeting_id[:12]}")
    table.add_column("Stage")
    table.add_column("Status")
    table.add_column("Attempt", justify="right")
    table.add_column("Started")
    table.add_column("Finished")
    table.add_column("Last error")

    for s in states:
        style = _STATUS_STYLE.get(s.status.value, "")
        table.add_row(
            s.stage.value,
            f"[{style}]{s.status.value}[/{style}]" if style else s.status.value,
            str(s.attempt),
            s.started_at.strftime("%Y-%m-%d %H:%M:%S") if s.started_at else "",
            s.finished_at.strftime("%Y-%m-%d %H:%M:%S") if s.finished_at else "",
            (s.last_error or "")[:60],
        )
    console.print(table)


@app.command("resume")
def resume_cmd(
    meeting_id: Optional[str] = typer.Argument(None, help="Meeting ID (omit with --all)"),
    all_meetings: bool = typer.Option(False, "--all", help="Resume every meeting with a failed stage"),
    from_stage: Optional[str] = typer.Option(None, "--from-stage", help="Start from this stage"),
):
    """Resume a meeting's pipeline from the first non-succeeded stage."""
    from meeting_minutes.pipeline import PipelineOrchestrator
    from meeting_minutes.pipeline_state import Stage, Status, get_stages
    from meeting_minutes.system3.db import PipelineStageORM

    config = _load_config()
    orchestrator = PipelineOrchestrator(config)

    parsed_stage: Stage | None = None
    if from_stage:
        try:
            parsed_stage = Stage(from_stage)
        except ValueError:
            err_console.print(f"[red]Unknown stage: {from_stage}[/red]")
            raise typer.Exit(code=1)

    if all_meetings:
        session = _get_db_session(config)
        try:
            failed_ids = [
                row[0]
                for row in session.query(PipelineStageORM.meeting_id)
                .filter(PipelineStageORM.status == Status.FAILED.value)
                .distinct()
                .all()
            ]
        finally:
            session.close()

        if not failed_ids:
            console.print("[dim]No meetings with failed stages found.[/dim]")
            return

        console.print(f"[bold]Resuming {len(failed_ids)} meeting(s)...[/bold]")
        for mid in failed_ids:
            try:
                asyncio.run(orchestrator.resume_from(mid, from_stage=parsed_stage))
            except Exception as exc:
                err_console.print(f"[red]Resume failed for {mid[:12]}: {exc}[/red]")
        return

    if not meeting_id:
        err_console.print("[red]Provide a meeting_id or use --all[/red]")
        raise typer.Exit(code=1)

    # Report which stages will run.
    session = _get_db_session(config)
    try:
        existing = {s.stage: s for s in get_stages(session, meeting_id)}
    finally:
        session.close()

    start = parsed_stage
    if start is None:
        for stage in Stage.ordered():
            state = existing.get(stage)
            if state is None or state.status != Status.SUCCEEDED:
                start = stage
                break

    if start is None:
        console.print(f"[dim]All stages already succeeded for {meeting_id[:12]}[/dim]")
        return

    start_idx = Stage.ordered().index(start)
    plan = [s.value for s in Stage.ordered()[start_idx:]]
    console.print(f"[bold]Resuming {meeting_id[:12]} — will run: {', '.join(plan)}[/bold]")

    try:
        asyncio.run(orchestrator.resume_from(meeting_id, from_stage=parsed_stage))
        console.print(f"[green]Resume complete.[/green]")
    except Exception as exc:
        err_console.print(f"[red]Resume failed: {exc}[/red]")
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# mm repair (HLT-1)
# ---------------------------------------------------------------------------


_HEALTH_STATUS_STYLE = {
    "ok": "green",
    "warn": "yellow",
    "fail": "red",
}


def _render_health_table(report) -> Table:
    table = Table(title=f"Health check — overall: {report.overall_status.upper()}")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Detail", overflow="fold")
    table.add_column("Fix hint", overflow="fold")

    for r in report.checks:
        style = _HEALTH_STATUS_STYLE.get(r.status, "")
        status_cell = f"[{style}]{r.status}[/{style}]" if style else r.status
        table.add_row(r.name, status_cell, r.detail, r.fix_hint or "")
    return table


@app.command("repair")
def repair_cmd(
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the plan, do not write."),
    check: Optional[str] = typer.Option(None, "--check", help="Repair only this check name."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
):
    """Run startup health checks and optionally repair failing ones."""
    from meeting_minutes.health import check_all, repair as run_repair

    config = _load_config()
    session = _get_db_session(config)

    try:
        report = check_all(session, config)
        console.print(_render_health_table(report))

        repairable = [r for r in report.checks if r.repairable and r.status != "ok"]
        if check is not None:
            repairable = [r for r in repairable if r.name == check]

        if not repairable:
            console.print("[dim]Nothing to repair.[/dim]")
            return

        if dry_run:
            log = run_repair(report, session, config, dry_run=True, only=check)
            console.print("[bold]Dry-run plan:[/bold]")
            for a in log.actions:
                console.print(f"  [{a['action']}] {a['check']}: {a['detail']}")
            return

        if not yes:
            names = ", ".join(r.name for r in repairable)
            confirmed = typer.confirm(f"Run repair for: {names}?")
            if not confirmed:
                console.print("Aborted.")
                return

        log = run_repair(report, session, config, dry_run=False, only=check)
        for a in log.actions:
            style = "green" if a["action"] not in ("error", "noop") else "yellow"
            console.print(f"  [{style}][{a['action']}][/{style}] {a['check']}: {a['detail']}")
        console.print("[green]Repair complete.[/green]")
    finally:
        session.close()


# ---------------------------------------------------------------------------
# mm doctor (ONB-1)
# ---------------------------------------------------------------------------


@app.command("doctor")
def doctor_cmd(
    as_json: bool = typer.Option(False, "--json", help="Emit the check list as JSON."),
):
    """Run the first-run diagnostic checks. Exits non-zero on any failure."""
    from meeting_minutes.doctor import run_checks

    config = _load_config()
    results = run_checks(config)

    if as_json:
        payload = {
            "checks": [r.to_dict() for r in results],
            "overall_status": (
                "fail" if any(r.status == "fail" for r in results)
                else "warn" if any(r.status == "warn" for r in results)
                else "ok"
            ),
        }
        console.print_json(data=payload)
    else:
        table = Table(title="mm doctor")
        table.add_column("#", justify="right", width=3)
        table.add_column("Check")
        table.add_column("Status")
        table.add_column("Detail", overflow="fold")
        table.add_column("Fix hint", overflow="fold")
        for i, r in enumerate(results, 1):
            style = _HEALTH_STATUS_STYLE.get(r.status, "")
            status_cell = f"[{style}]{r.status}[/{style}]" if style else r.status
            fix = r.fix_hint
            if r.fix_command:
                fix = f"{fix}\n  $ {r.fix_command}" if fix else f"$ {r.fix_command}"
            table.add_row(str(i), r.name, status_cell, r.detail, fix)
        console.print(table)

    if any(r.status == "fail" for r in results):
        raise typer.Exit(code=1)


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
    db_path = resolve_db_path(config.storage.sqlite_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Auto-backup existing database before initialization
    if db_path.exists():
        try:
            from meeting_minutes.backup import backup_database

            backup_dir = Path(config.backup.backup_dir)
            backup_file = backup_database(db_path, backup_dir, prefix="pre_init")
            console.print(f"[dim]Backed up existing database before initialization: {backup_file.name}[/dim]")
        except Exception as exc:
            console.print(f"[yellow]Warning: Could not backup database: {exc}[/yellow]")

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
    import signal
    import socket
    import uvicorn

    def _port_in_use(host: str, port: int) -> int | None:
        """Return the PID using the port, or None if the port is free."""
        import subprocess
        try:
            result = subprocess.run(
                ["lsof", "-ti", f"tcp:{port}"],
                capture_output=True, text=True, timeout=3,
            )
            if result.returncode == 0 and result.stdout.strip():
                return int(result.stdout.strip().split("\n")[0])
        except Exception:
            pass
        # Fallback: just try to bind
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind((host, port))
                return None
        except OSError:
            return -1  # In use but PID unknown

    pid = _port_in_use(host, port)

    if pid is not None:
        # Detect TTY — launchd/systemd/docker have no terminal, so prompts hang.
        # Non-interactive callers (like the launchd-managed service) refuse to
        # drift to a different port: silent drift to 8081 is exactly the bug we
        # were chasing in upgrades. launchd's KeepAlive will respawn us once
        # the port is actually free.
        is_interactive = sys.stdin.isatty() and sys.stdout.isatty()

        if not is_interactive:
            err_console.print(
                f"[red]Port {port} is already in use; refusing to start on a different port.[/red]"
            )
            if pid > 0:
                err_console.print(f"[dim]Held by PID {pid}.[/dim]")
            raise typer.Exit(code=1)

        if pid > 0:
            console.print(f"[yellow]Port {port} is already in use by PID {pid}.[/yellow]")
            choice = typer.prompt(
                "Kill the process, specify a different port, or abort?",
                type=typer.Choice(["kill", "port", "abort"], case_sensitive=False),
                default="abort",
            )
        else:
            console.print(f"[yellow]Port {port} is already in use.[/yellow]")
            choice = typer.prompt(
                "Specify a different port, or abort?",
                type=typer.Choice(["port", "abort"], case_sensitive=False),
                default="abort",
            )

        if choice == "kill" and pid > 0:
            import os
            try:
                os.kill(pid, signal.SIGTERM)
                console.print(f"  [green]Killed PID {pid}[/green]")
                import time
                time.sleep(1)
            except ProcessLookupError:
                console.print(f"  [dim]PID {pid} already exited[/dim]")
            except PermissionError:
                err_console.print(f"[red]Permission denied killing PID {pid}. Try: sudo kill {pid}[/red]")
                raise typer.Exit(code=1)
        elif choice == "port":
            new_port = typer.prompt("Enter a port number", type=int)
            if not (1 <= new_port <= 65535):
                err_console.print(f"[red]Port {new_port} is out of range.[/red]")
                raise typer.Exit(code=1)
            if _port_in_use(host, new_port) is not None:
                err_console.print(f"[red]Port {new_port} is also in use.[/red]")
                raise typer.Exit(code=1)
            port = new_port
            console.print(f"  [green]Using port {port}[/green]")
        else:
            raise typer.Exit(code=0)

    console.print(f"[green]Starting API server on {host}:{port}[/green]")
    uvicorn.run(
        "meeting_minutes.api.main:app",
        host=host,
        port=port,
        reload=False,
    )


# ---------------------------------------------------------------------------
# mm backup
# ---------------------------------------------------------------------------


@backup_app.callback(invoke_without_command=True)
def backup_now(ctx: typer.Context):
    """Create a database backup now."""
    if ctx.invoked_subcommand is not None:
        return

    from meeting_minutes.backup import backup_database, rotate_backups

    config = _load_config()
    db_path = resolve_db_path(config.storage.sqlite_path)
    backup_dir = Path(config.backup.backup_dir)

    if not db_path.exists():
        err_console.print(f"[red]Database not found: {db_path}[/red]")
        raise typer.Exit(code=1)

    backup_file = backup_database(db_path, backup_dir)
    deleted = rotate_backups(backup_dir)
    console.print(f"[green]Backup created: {backup_file}[/green]")
    if deleted:
        console.print(f"[dim]Rotated {deleted} old backup(s)[/dim]")


@backup_app.command("list")
def backup_list():
    """List all available backups."""
    from meeting_minutes.backup import list_backups

    config = _load_config()
    backups = list_backups(config.backup.backup_dir)

    if not backups:
        console.print("[yellow]No backups found.[/yellow]")
        return

    table = Table(title="Database Backups")
    table.add_column("Filename")
    table.add_column("Size")
    table.add_column("Created")
    for b in backups:
        table.add_row(b["filename"], f"{b['size_mb']} MB", b["created"])
    console.print(table)


@backup_app.command("restore")
def backup_restore(
    filename: str = typer.Argument(..., help="Backup filename to restore from"),
):
    """Restore database from a backup."""
    from meeting_minutes.backup import restore_backup

    config = _load_config()
    backup_dir = Path(config.backup.backup_dir)
    backup_file = backup_dir / filename
    db_path = resolve_db_path(config.storage.sqlite_path)

    if not backup_file.exists():
        err_console.print(f"[red]Backup not found: {backup_file}[/red]")
        raise typer.Exit(code=1)

    confirmed = typer.confirm(
        f"Restore from {filename}? Current database will be backed up first."
    )
    if not confirmed:
        console.print("Aborted.")
        return

    restore_backup(backup_file, db_path)
    console.print(f"[green]Database restored from {filename}[/green]")
    console.print(f"[dim]Previous database saved as {db_path}.pre_restore[/dim]")


# ---------------------------------------------------------------------------
# mm cleanup
# ---------------------------------------------------------------------------


@app.command("cleanup")
def cleanup_cmd():
    """Enforce retention policies — delete old files."""
    from meeting_minutes.retention import enforce_retention

    config = _load_config()
    deleted = enforce_retention(config)
    total = sum(deleted.values())
    if total:
        console.print(f"[green]Cleaned up {total} files: {deleted}[/green]")
    else:
        console.print("[dim]No files to clean up.[/dim]")


# ---------------------------------------------------------------------------
# mm embed
# ---------------------------------------------------------------------------


def _embed_diagnose() -> None:
    """Diagnostic for the semantic search setup."""
    from sqlalchemy import text as _sql_text
    from meeting_minutes.system3.db import get_session_factory, EmbeddingChunkORM

    config = _load_config()
    db_path = resolve_db_path(config.storage.sqlite_path)

    console.print(f"[bold]Semantic search diagnostic[/bold]\n")
    console.print(f"DB path: {db_path}")
    console.print(f"DB exists: {db_path.exists()}")

    # Check sqlite-vec import
    try:
        import sqlite_vec
        console.print(f"[green]✓[/green] sqlite_vec package: {sqlite_vec.__file__}")
    except ImportError as e:
        console.print(f"[red]✗[/red] sqlite_vec package not installed: {e}")
        return

    # Check sentence-transformers import
    try:
        import sentence_transformers
        console.print(f"[green]✓[/green] sentence_transformers: {sentence_transformers.__version__}")
    except ImportError as e:
        console.print(f"[red]✗[/red] sentence_transformers not installed: {e}")
        return

    session_factory = get_session_factory(f"sqlite:///{db_path}")
    session = session_factory()

    # Check sqlite-vec loaded in this session
    try:
        session.execute(_sql_text("SELECT vec_version()")).fetchone()
        console.print(f"[green]✓[/green] sqlite-vec extension loaded")
    except Exception as e:
        console.print(f"[red]✗[/red] sqlite-vec not loaded in DB session: {e}")
        session.close()
        return

    # Count chunks
    chunk_count = session.query(EmbeddingChunkORM).count()
    console.print(f"  Embedding chunks in DB: [cyan]{chunk_count}[/cyan]")

    # Count vectors in virtual table
    try:
        vec_count = session.execute(_sql_text("SELECT COUNT(*) FROM embedding_vectors")).scalar()
        console.print(f"  Vectors in embedding_vectors: [cyan]{vec_count}[/cyan]")
        if vec_count != chunk_count:
            console.print(
                f"  [yellow]⚠ Mismatch: {chunk_count} chunks but {vec_count} vectors. "
                f"Try: mm embed --force[/yellow]"
            )
    except Exception as e:
        console.print(f"[red]✗[/red] Failed to count vectors: {e}")
        session.close()
        return

    # Try a test query
    if vec_count and vec_count > 0:
        try:
            from meeting_minutes.embeddings import EmbeddingEngine
            engine = EmbeddingEngine(config)
            results = engine.search("What was discussed", session, limit=5)
            console.print(f"  [green]✓[/green] Test query returned {len(results)} results")
            if results:
                top = results[0]
                console.print(f"    Top match: [{top['chunk_type']}] {top['text'][:80]}...")
                console.print(f"    Distance: {top['distance']:.3f} (lower = closer)")
        except Exception as e:
            console.print(f"[red]✗[/red] Test query failed: {e}")

    session.close()


@app.command("embed")
def embed_cmd(
    meeting_id: str = typer.Argument(None, help="Meeting ID to embed (omit for all)"),
    force: bool = typer.Option(False, "--force", "-f", help="Re-embed even if already indexed"),
    check: bool = typer.Option(False, "--check", help="Diagnose the semantic search setup (vectors, counts, test query)"),
):
    """Generate semantic search embeddings for meetings.

    Run without arguments to embed all meetings. On first run after upgrade,
    this backfills embeddings for all existing meetings (~2s per meeting).
    """
    if check:
        _embed_diagnose()
        return

    from meeting_minutes.embeddings import EmbeddingEngine
    from meeting_minutes.system3.db import get_session_factory, EmbeddingChunkORM
    from rich.progress import Progress

    config = _load_config()
    db_path = resolve_db_path(config.storage.sqlite_path)
    data_dir = Path(config.data_dir).expanduser()
    session_factory = get_session_factory(f"sqlite:///{db_path}")
    session = session_factory()
    engine = EmbeddingEngine(config)

    if meeting_id:
        # Single meeting
        console.print(f"[bold]Embedding meeting {meeting_id[:12]}...[/bold]")
        count = engine.index_meeting(meeting_id, session, data_dir)
        console.print(f"[green]✓ Indexed {count} chunks[/green]")
    else:
        # All meetings
        minutes_dir = data_dir / "minutes"
        if not minutes_dir.exists():
            console.print("[yellow]No minutes directory found.[/yellow]")
            return

        meeting_files = sorted(minutes_dir.glob("*.json"))
        if not meeting_files:
            console.print("[yellow]No meetings found to embed.[/yellow]")
            return

        # Check which already have embeddings
        if not force:
            existing = {r[0] for r in session.query(EmbeddingChunkORM.meeting_id).distinct().all()}
            to_embed = [f for f in meeting_files if f.stem not in existing]
        else:
            to_embed = meeting_files

        if not to_embed:
            console.print(f"[dim]All {len(meeting_files)} meetings already embedded. Use --force to re-embed.[/dim]")
            return

        console.print(f"[bold]Embedding {len(to_embed)} meeting(s)...[/bold]")
        total_chunks = 0

        with Progress() as progress:
            task = progress.add_task("Embedding", total=len(to_embed))
            for mf in to_embed:
                mid = mf.stem
                try:
                    count = engine.index_meeting(mid, session, data_dir)
                    total_chunks += count
                except Exception as exc:
                    console.print(f"  [yellow]⚠ {mid[:8]}: {exc}[/yellow]")
                progress.advance(task)

        console.print(f"\n[green]✓ Indexed {total_chunks} chunks across {len(to_embed)} meetings[/green]")

    session.close()


# ---------------------------------------------------------------------------
# mm generate-key
# ---------------------------------------------------------------------------


@app.command("generate-key")
def generate_key_cmd():
    """Generate a new encryption key for at-rest encryption."""
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        err_console.print("[red]cryptography package not installed. Run: pip install cryptography[/red]")
        raise typer.Exit(code=1)

    key = Fernet.generate_key().decode()
    console.print(f"[green]Generated encryption key:[/green]")
    console.print(f"[bold]{key}[/bold]")
    console.print(f"\n[dim]Add this to config.yaml:[/dim]")
    console.print(f"[dim]security:[/dim]")
    console.print(f"[dim]  encryption_enabled: true[/dim]")
    console.print(f'[dim]  encryption_key: "{key}"[/dim]')


# ---------------------------------------------------------------------------
# mm service (macOS Launch Agent management)
# ---------------------------------------------------------------------------

PLIST_NAME = "com.meetingminutes.server"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{PLIST_NAME}.plist"


def _get_project_root() -> Path:
    """Get the project root directory."""
    return Path(__file__).resolve().parent.parent.parent.parent


def _get_mm_binary() -> str:
    """Get the path to the mm binary."""
    # Prefer the venv binary
    venv_mm = _get_project_root() / ".venv" / "bin" / "mm"
    if venv_mm.exists():
        return str(venv_mm)
    # Fallback to whatever mm is in PATH
    return str(sys.executable).replace("python", "mm")


def _read_plist_port(plist_path: Path) -> int | None:
    """Extract the --port argument from an installed launchd plist.

    Returns the configured port, or None if the plist can't be parsed or no
    --port argument is present.
    """
    import plistlib

    try:
        with open(plist_path, "rb") as f:
            data = plistlib.load(f)
    except Exception:
        return None
    args = data.get("ProgramArguments") or []
    for i, arg in enumerate(args):
        if arg in ("--port", "-p") and i + 1 < len(args):
            try:
                return int(args[i + 1])
            except (TypeError, ValueError):
                return None
    return None


def _wait_for_port_release(port: int, timeout: float, escalate_after: float) -> bool:
    """Poll until the TCP port has no listener, escalating SIGTERM → SIGKILL.

    `launchctl unload` returns as soon as SIGTERM is delivered, but the old
    process keeps the listening socket bound until it actually exits. Without
    waiting, a follow-up `launchctl load` will spawn a new server that races
    the dying one for the port — which is how upgrades end up on 8081.

    After `escalate_after` seconds the original PID gets SIGKILL'd. Returns
    True when the port is free, False if the timeout expired.
    """
    import os
    import signal as _signal
    import socket
    import subprocess
    import time

    def _holder_pid() -> int | None:
        try:
            r = subprocess.run(
                ["lsof", "-ti", f"tcp:{port}"],
                capture_output=True, text=True, timeout=2,
            )
            if r.returncode == 0 and r.stdout.strip():
                return int(r.stdout.strip().split("\n")[0])
        except Exception:
            pass
        # Fallback: lsof not available — try binding.
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
            return None
        except OSError:
            return -1

    deadline = time.monotonic() + timeout
    escalate_at = time.monotonic() + escalate_after
    escalated = False
    initial_pid = _holder_pid()
    if initial_pid is None:
        return True

    while time.monotonic() < deadline:
        if _holder_pid() is None:
            return True
        if not escalated and time.monotonic() >= escalate_at and initial_pid and initial_pid > 0:
            try:
                os.kill(initial_pid, _signal.SIGKILL)
                console.print(f"  [dim]Port {port} still held by PID {initial_pid} — sent SIGKILL[/dim]")
            except ProcessLookupError:
                pass
            except PermissionError:
                err_console.print(f"  [yellow]Cannot SIGKILL PID {initial_pid} (permission denied)[/yellow]")
            escalated = True
        time.sleep(0.2)
    return _holder_pid() is None


def _generate_plist(project_root: Path, mm_binary: str, port: int = 8080) -> str:
    """Generate the launchd plist XML."""
    logs_dir = project_root / "logs"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{PLIST_NAME}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{mm_binary}</string>
        <string>serve</string>
        <string>--port</string>
        <string>{port}</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{project_root}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{logs_dir / "server.log"}</string>
    <key>StandardErrorPath</key>
    <string>{logs_dir / "server.err"}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
"""


@service_app.command("install")
def service_install(
    port: int = typer.Option(8080, "--port", "-p", help="Server port"),
):
    """Install the macOS Launch Agent for auto-start on login."""
    import platform

    if platform.system() != "Darwin":
        err_console.print("[red]Service management is only supported on macOS.[/red]")
        raise typer.Exit(code=1)

    project_root = _get_project_root()
    mm_binary = _get_mm_binary()

    # Create logs directory
    (project_root / "logs").mkdir(exist_ok=True)

    # Generate and write plist
    plist_content = _generate_plist(project_root, mm_binary, port)
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.write_text(plist_content)

    console.print(f"[green]Service installed: {PLIST_PATH}[/green]")
    console.print(f"[dim]Server will auto-start on login (port {port})[/dim]")
    console.print("[dim]Run 'mm service start' to start now[/dim]")


@service_app.command("uninstall")
def service_uninstall():
    """Remove the macOS Launch Agent."""
    if PLIST_PATH.exists():
        # Stop first if running
        import subprocess

        subprocess.run(["launchctl", "unload", str(PLIST_PATH)], capture_output=True)
        PLIST_PATH.unlink()
        console.print("[green]Service uninstalled.[/green]")
    else:
        console.print("[yellow]Service not installed.[/yellow]")


@service_app.command("start")
def service_start():
    """Start the service now."""
    if not PLIST_PATH.exists():
        err_console.print("[red]Service not installed. Run 'mm service install' first.[/red]")
        raise typer.Exit(code=1)

    import subprocess

    result = subprocess.run(["launchctl", "load", str(PLIST_PATH)], capture_output=True, text=True)
    if result.returncode == 0:
        console.print("[green]Service started.[/green]")
        console.print("[dim]Open http://localhost:8080 in your browser[/dim]")
    else:
        # May already be loaded
        if "already loaded" in result.stderr.lower() or "already bootstrapped" in result.stderr.lower():
            console.print("[yellow]Service is already running.[/yellow]")
        else:
            err_console.print(f"[red]Failed to start: {result.stderr}[/red]")


@service_app.command("stop")
def service_stop():
    """Stop the service."""
    if not PLIST_PATH.exists():
        err_console.print("[red]Service not installed.[/red]")
        raise typer.Exit(code=1)

    import subprocess

    subprocess.run(["launchctl", "unload", str(PLIST_PATH)], capture_output=True)
    console.print("[green]Service stopped.[/green]")


@service_app.command("status")
def service_status():
    """Show service status."""
    import subprocess

    if not PLIST_PATH.exists():
        console.print("[yellow]Service not installed.[/yellow]")
        return

    result = subprocess.run(
        ["launchctl", "list", PLIST_NAME],
        capture_output=True,
        text=True,
    )

    if result.returncode == 0:
        # Parse the output for PID and status
        lines = result.stdout.strip().split("\n")
        console.print("[green]Service is running.[/green]")
        for line in lines:
            console.print(f"  [dim]{line}[/dim]")

        # Check if port is actually responding
        import urllib.request

        try:
            urllib.request.urlopen("http://localhost:8080/api/stats", timeout=2)
            console.print("  [green]API responding at http://localhost:8080[/green]")
        except Exception:
            console.print("  [yellow]! API not yet responding (may be starting up)[/yellow]")
    else:
        console.print("[yellow]Service is not running.[/yellow]")
        console.print("[dim]Run 'mm service start' to start it.[/dim]")


@service_app.command("logs")
def service_logs(
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow log output"),
    lines: int = typer.Option(50, "--lines", "-n", help="Number of lines to show"),
):
    """Show server logs."""
    import subprocess

    project_root = _get_project_root()
    log_file = project_root / "logs" / "server.log"
    err_file = project_root / "logs" / "server.err"

    if not log_file.exists() and not err_file.exists():
        console.print("[yellow]No log files found. Start the service first.[/yellow]")
        return

    if follow:
        # Use tail -f on both log files
        console.print(f"[dim]Following {log_file.name} and {err_file.name} (Ctrl+C to stop)...[/dim]")
        try:
            cmd = ["tail", "-f"]
            if log_file.exists():
                cmd.append(str(log_file))
            if err_file.exists():
                cmd.append(str(err_file))
            subprocess.run(cmd)
        except KeyboardInterrupt:
            pass
    else:
        # Show last N lines
        for f in [log_file, err_file]:
            if f.exists():
                console.print(f"\n[bold]-- {f.name} --[/bold]")
                result = subprocess.run(
                    ["tail", f"-{lines}", str(f)],
                    capture_output=True,
                    text=True,
                )
                console.print(result.stdout)


# ---------------------------------------------------------------------------
# mm upgrade
# ---------------------------------------------------------------------------


def _deep_merge_user_into_shipped(user: dict, shipped: dict) -> tuple[dict, list[str]]:
    """Overlay user values onto the shipped config. User values win for keys the
    user set; keys that exist only in the shipped config are added.

    Returns (merged, added_keys) where added_keys is a dotted-path list of keys
    that were new in the shipped config — surfaced to the user so they know what
    new knobs are available after the upgrade.
    """
    added: list[str] = []

    def _merge(u: object, s: object, path: str) -> object:
        if isinstance(u, dict) and isinstance(s, dict):
            out: dict = {}
            for k, uv in u.items():
                if k in s:
                    out[k] = _merge(uv, s[k], f"{path}.{k}" if path else k)
                else:
                    out[k] = uv  # user-only key — preserve as-is
            for k, sv in s.items():
                if k not in u:
                    out[k] = sv
                    added.append(f"{path}.{k}" if path else k)
            return out
        # Leaf or type-mismatched node: user wins.
        return u

    merged = _merge(user, shipped, "")
    if not isinstance(merged, dict):
        merged = {}
    return merged, added


def _preserve_user_config_through_upgrade(project_root, run_merge):
    """Run `run_merge()` with the user's config/config.yaml protected.

    If config/config.yaml has uncommitted local edits:
    1. Read the user's version, reset the file to HEAD so the merge can fast-forward.
    2. Call `run_merge()`.
    3. Deep-merge the user's values on top of whatever config is on disk after
       the merge and write the result back. Report any new keys from upstream.

    If the file is clean, just run the merge — there's nothing to preserve.
    """
    import subprocess
    from pathlib import Path

    import yaml

    config_path = Path(project_root) / "config" / "config.yaml"
    rel_path = "config/config.yaml"

    # Detect dirty state.
    status = subprocess.run(
        ["git", "status", "--porcelain", "--", rel_path],
        capture_output=True, text=True, cwd=project_root,
    )
    dirty = bool(status.stdout.strip())

    if not dirty or not config_path.exists():
        run_merge()
        return

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            user_yaml_text = f.read()
        user_cfg = yaml.safe_load(user_yaml_text) or {}
    except Exception as exc:
        err_console.print(f"[yellow]Could not read {rel_path} ({exc}). Proceeding without preservation.[/yellow]")
        run_merge()
        return

    # Keep a timestamped backup in case the merge goes sideways.
    from datetime import datetime
    backup_path = config_path.with_suffix(
        f".yaml.user-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    )
    try:
        with open(backup_path, "w", encoding="utf-8") as f:
            f.write(user_yaml_text)
    except Exception:
        backup_path = None

    # Reset the tracked file so `git merge --ff-only` won't trip on it.
    reset = subprocess.run(
        ["git", "checkout", "--", rel_path],
        capture_output=True, text=True, cwd=project_root,
    )
    if reset.returncode != 0:
        err_console.print(f"[red]Could not reset {rel_path} for merge: {reset.stderr}[/red]")
        raise typer.Exit(code=1)

    try:
        run_merge()
    except Exception:
        # Merge failed — restore the user's file so we leave no worse state behind.
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(user_yaml_text)
        except Exception:
            pass
        raise

    # Merge user values back on top of the (possibly updated) shipped config.
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            shipped_cfg = yaml.safe_load(f) or {}
    except Exception:
        shipped_cfg = {}

    merged, added = _deep_merge_user_into_shipped(user_cfg, shipped_cfg)

    try:
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(merged, f, sort_keys=False, default_flow_style=False)
        console.print("  [green]✓[/green] User config preserved in config/config.yaml")
        if added:
            console.print(
                f"  [dim]New config keys from upstream: {', '.join(added)} — review defaults in config/config.yaml[/dim]"
            )
        if backup_path:
            console.print(f"  [dim]Pre-upgrade backup: {backup_path.name}[/dim]")
    except Exception as exc:
        err_console.print(f"[red]Failed to write merged config: {exc}[/red]")
        if backup_path:
            err_console.print(f"[dim]Your original is at {backup_path}[/dim]")
        raise typer.Exit(code=1)


@app.command("upgrade")
def upgrade_cmd(
    restart: bool = typer.Option(True, "--restart/--no-restart", help="Restart the service after upgrading"),
    branch: str = typer.Option("main", "--branch", "-b", help="Branch to pull from (default: main)"),
):
    """Pull latest code from GitHub and rebuild everything. Always upgrades from main by default."""
    import os
    import subprocess

    project_root = _get_project_root()

    # 1. Check for uncommitted changes
    console.print("[bold][1/5] Checking for local changes...[/bold]")
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True, text=True, cwd=project_root,
    )
    dirty_lines = [line for line in result.stdout.splitlines() if line.strip()]
    # User edits to config/config.yaml are preserved automatically — no need to warn.
    non_config_dirty = [line for line in dirty_lines if line[3:].strip() != "config/config.yaml"]
    if non_config_dirty:
        console.print("[yellow]Warning: You have uncommitted local changes.[/yellow]")
        console.print("\n".join(non_config_dirty))
        if not typer.confirm("Continue anyway? Local changes will be preserved (git pull may fail if there are conflicts)."):
            raise typer.Exit(code=0)

    # 2. Pull latest from the target branch
    console.print(f"[bold][2/5] Pulling latest code from origin/{branch}...[/bold]")

    # Detect current branch
    cur_branch_result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, cwd=project_root,
    )
    current_branch = cur_branch_result.stdout.strip()

    def _do_switch_and_merge() -> None:
        # Switch to target branch if not already on it. Runs inside the config
        # preservation wrapper so a dirty config/config.yaml can't block the
        # checkout — the wrapper has already reset that file to HEAD.
        if current_branch != branch:
            console.print(f"  [yellow]Currently on '{current_branch}', switching to '{branch}'...[/yellow]")
            switch_result = subprocess.run(
                ["git", "checkout", branch],
                capture_output=True, text=True, cwd=project_root,
            )
            if switch_result.returncode != 0:
                err_console.print(f"[red]Failed to switch to '{branch}': {switch_result.stderr}[/red]")
                err_console.print(f"[dim]Stay on '{current_branch}' or resolve conflicts manually.[/dim]")
                raise typer.Exit(code=1)
            console.print(f"  [green]✓[/green] Switched to '{branch}'")

        # Fetch + fast-forward merge from origin/<branch>
        fetch_result = subprocess.run(
            ["git", "fetch", "origin", branch],
            capture_output=True, text=True, cwd=project_root,
        )
        if fetch_result.returncode != 0:
            err_console.print(f"[red]git fetch failed: {fetch_result.stderr}[/red]")
            raise typer.Exit(code=1)

        result = subprocess.run(
            ["git", "merge", "--ff-only", f"origin/{branch}"],
            capture_output=True, text=True, cwd=project_root,
        )
        if result.returncode != 0:
            if "not possible to fast-forward" in result.stderr.lower() or "non-fast-forward" in result.stderr.lower():
                err_console.print(f"[red]Cannot fast-forward. You have local commits on '{branch}' that diverge from origin/{branch}.[/red]")
                err_console.print("[dim]Resolve manually with: git pull --rebase origin " + branch + "[/dim]")
            else:
                err_console.print(f"[red]git merge failed: {result.stderr}[/red]")
            raise typer.Exit(code=1)
        console.print(f"  {result.stdout.strip() or 'Already up to date.'}")

    # Protect the user's config/config.yaml across the branch switch + merge:
    # stash local edits, switch+merge, then re-apply user values on top of the
    # (possibly updated) shipped defaults.
    _preserve_user_config_through_upgrade(project_root, _do_switch_and_merge)

    # 2b. Refresh macOS native deps for WeasyPrint (idempotent; Homebrew-only).
    # Needed for PDF export (EXP-1) — existing installs predate this requirement.
    import platform as _platform

    if _platform.system() == "Darwin":
        import shutil as _shutil

        if _shutil.which("brew"):
            console.print("[bold][2b/5] Checking WeasyPrint native libs (PDF export)...[/bold]")
            weasy_deps = ("pango", "cairo", "gdk-pixbuf", "libffi")
            missing = []
            for dep in weasy_deps:
                probe = subprocess.run(
                    ["brew", "list", "--formula", dep],
                    capture_output=True, text=True,
                )
                if probe.returncode != 0:
                    missing.append(dep)
            if not missing:
                console.print("  [green]✓[/green] pango, cairo, gdk-pixbuf, libffi already installed")
            else:
                console.print(f"  [dim]Installing: {', '.join(missing)}[/dim]")
                install_result = subprocess.run(
                    ["brew", "install", *missing],
                    capture_output=True, text=True,
                )
                if install_result.returncode == 0:
                    console.print("  [green]✓[/green] WeasyPrint native libs installed")
                else:
                    console.print(
                        "  [yellow]! brew install failed — PDF export may not work. "
                        f"Error: {install_result.stderr.strip()[:200]}[/yellow]"
                    )
        # else: silently skip — non-brew macOS users manage their own libs.

    # 3. Install Python dependencies.
    #
    # Extras: always install ``dev`` (test deps) and ``diarize-cloud`` (the
    # pyannoteAI SDK, platform-agnostic and tiny). Add ``diarize-mlx`` only
    # on Apple Silicon — its only dependency is the ``mlx`` wheel, which is
    # Apple-Silicon-only and would fail on Intel macOS / Linux / Windows.
    # This guarantees the engine the user picks in the settings page is
    # actually runnable post-upgrade, instead of silently falling back.
    console.print("[bold][3/5] Updating Python dependencies...[/bold]")
    venv_pip = project_root / ".venv" / "bin" / "pip"
    if not venv_pip.exists():
        err_console.print("[red]Virtual environment not found. Run install.sh first.[/red]")
        raise typer.Exit(code=1)
    extras = ["dev", "diarize-cloud"]
    if _platform.system() == "Darwin" and _platform.machine() == "arm64":
        extras.append("diarize-mlx")
    extras_spec = ",".join(extras)
    result = subprocess.run(
        [str(venv_pip), "install", "--quiet", "-e", f".[{extras_spec}]"],
        capture_output=True, text=True, cwd=project_root,
    )
    if result.returncode != 0:
        err_console.print(f"[red]pip install failed: {result.stderr}[/red]")
        raise typer.Exit(code=1)
    console.print(f"  [green]✓[/green] Python dependencies updated (extras: {extras_spec})")

    # 3b. Ensure whisper.cpp engine is installed (best effort, hardware-aware)
    pywhispercpp_check = subprocess.run(
        [str(venv_pip.parent / "python"), "-c", "import pywhispercpp"],
        capture_output=True, text=True, cwd=project_root,
    )
    if pywhispercpp_check.returncode != 0:
        console.print("  [dim]Installing Whisper.cpp engine (hardware-optimized)...[/dim]")
        import platform as _platform
        env = os.environ.copy()
        if _platform.system() == "Darwin" and _platform.machine() == "arm64":
            env["WHISPER_METAL"] = "1"
        elif _platform.system() == "Linux":
            try:
                subprocess.check_output(["nvidia-smi", "-L"], stderr=subprocess.DEVNULL, timeout=2)
                env["WHISPER_CUDA"] = "1"
            except Exception:
                pass
        wcpp_result = subprocess.run(
            [str(venv_pip), "install", "--quiet", "--no-binary=pywhispercpp", "pywhispercpp", "psutil"],
            capture_output=True, text=True, cwd=project_root, env=env,
        )
        if wcpp_result.returncode == 0:
            console.print("  [green]✓[/green] Whisper.cpp engine installed")
        else:
            console.print("  [dim]Whisper.cpp install skipped (Faster Whisper still works)[/dim]")

    # 4. Rebuild frontend
    console.print("[bold][4/5] Rebuilding web frontend...[/bold]")
    web_dir = project_root / "web"
    npm_path = "npm"

    # Install node_modules if needed
    if not (web_dir / "node_modules").exists():
        subprocess.run(
            [npm_path, "install", "--silent"],
            capture_output=True, text=True, cwd=web_dir,
        )

    result = subprocess.run(
        [npm_path, "run", "build"],
        capture_output=True, text=True, cwd=web_dir,
    )
    if result.returncode != 0:
        err_console.print(f"[red]Frontend build failed: {result.stderr}[/red]")
        raise typer.Exit(code=1)
    console.print("  [green]✓[/green] Frontend rebuilt")

    # 5. Restart service if running
    console.print("[bold][5/5] Restarting service...[/bold]")
    if restart and PLIST_PATH.exists():
        plist_port = _read_plist_port(PLIST_PATH)
        subprocess.run(["launchctl", "unload", str(PLIST_PATH)], capture_output=True)
        # Wait for the old process to release the port before reloading. Without
        # this, launchctl load races the dying uvicorn and the new server picks
        # a different port (or fails to bind).
        if plist_port is not None:
            freed = _wait_for_port_release(plist_port, timeout=15.0, escalate_after=5.0)
            if not freed:
                err_console.print(
                    f"  [yellow]Port {plist_port} still held after 15s — loading anyway; "
                    "service may fail to start.[/yellow]"
                )
        result = subprocess.run(["launchctl", "load", str(PLIST_PATH)], capture_output=True, text=True)
        if result.returncode == 0 or "already" in result.stderr.lower():
            console.print("  [green]✓[/green] Service restarted")
        else:
            err_console.print(f"  [yellow]Could not restart service: {result.stderr}[/yellow]")
    else:
        console.print("  [dim]Skipped (no service installed or --no-restart)[/dim]")

    console.print("\n[green bold]Upgrade complete![/green bold]")


# ---------------------------------------------------------------------------
# mm series (REC-1)
# ---------------------------------------------------------------------------


@series_app.command("detect")
def series_detect_cmd():
    """Run recurring-meeting detection and report changes."""
    from meeting_minutes.system3.series import detect_and_upsert

    session = _get_db_session()
    try:
        summary = detect_and_upsert(session)
    finally:
        session.close()

    total = len(summary.created) + len(summary.updated) + len(summary.unchanged)
    if total == 0:
        console.print("[dim]No meetings match the recurrence heuristic yet.[/dim]")
        return

    if summary.created:
        console.print(f"[green]Created {len(summary.created)} series:[/green]")
        for t in summary.created:
            console.print(f"  + {t}")
    if summary.updated:
        console.print(f"[yellow]Updated {len(summary.updated)} series:[/yellow]")
        for t in summary.updated:
            console.print(f"  ~ {t}")
    if summary.unchanged:
        console.print(f"[dim]{len(summary.unchanged)} series unchanged.[/dim]")


@series_app.command("list")
def series_list_cmd():
    """List all detected series."""
    from meeting_minutes.system3.db import (
        MeetingORM,
        MeetingSeriesMemberORM,
        MeetingSeriesORM,
    )

    session = _get_db_session()
    try:
        rows = (
            session.query(MeetingSeriesORM)
            .order_by(MeetingSeriesORM.last_detected_at.desc())
            .all()
        )

        if not rows:
            console.print("[yellow]No series detected. Run: mm series detect[/yellow]")
            return

        table = Table(title="Meeting Series")
        table.add_column("Series ID", style="dim", width=16)
        table.add_column("Title")
        table.add_column("Type")
        table.add_column("Cadence")
        table.add_column("Members", justify="right")
        table.add_column("Last meeting")

        for s in rows:
            member_ids = [
                m.meeting_id
                for m in session.query(MeetingSeriesMemberORM).filter_by(series_id=s.series_id).all()
            ]
            last_meeting = None
            if member_ids:
                last_meeting = (
                    session.query(MeetingORM)
                    .filter(MeetingORM.meeting_id.in_(member_ids))
                    .order_by(MeetingORM.date.desc())
                    .first()
                )
            last_date = (
                last_meeting.date.strftime("%Y-%m-%d") if last_meeting and last_meeting.date else ""
            )
            table.add_row(
                s.series_id,
                s.title,
                s.meeting_type,
                s.cadence or "",
                str(len(member_ids)),
                last_date,
            )

        console.print(table)
    finally:
        session.close()


@series_app.command("show")
def series_show_cmd(
    series_id: str = typer.Argument(..., help="Series ID"),
):
    """Show detail for a single series."""
    from meeting_minutes.system3.db import (
        MeetingORM,
        MeetingSeriesMemberORM,
        MeetingSeriesORM,
    )
    from meeting_minutes.system3.series import series_aggregates

    session = _get_db_session()
    try:
        series = session.get(MeetingSeriesORM, series_id)
        if series is None:
            err_console.print(f"[red]Series not found: {series_id}[/red]")
            raise typer.Exit(code=1)

        member_ids = [
            m.meeting_id
            for m in session.query(MeetingSeriesMemberORM).filter_by(series_id=series_id).all()
        ]
        members = (
            session.query(MeetingORM)
            .filter(MeetingORM.meeting_id.in_(member_ids))
            .order_by(MeetingORM.date.asc())
            .all()
        )

        header = [
            f"[bold]{series.title}[/bold]",
            f"Type: {series.meeting_type}",
            f"Cadence: {series.cadence or 'irregular'}",
            f"Members: {len(members)}",
        ]
        console.print(Panel("\n".join(header), title=series.series_id, border_style="blue"))

        if members:
            table = Table(title="Members")
            table.add_column("Meeting ID", style="dim", width=38)
            table.add_column("Title")
            table.add_column("Date")
            for m in members:
                table.add_row(
                    m.meeting_id,
                    m.title or "",
                    m.date.strftime("%Y-%m-%d") if m.date else "",
                )
            console.print(table)

        agg = series_aggregates(session, series_id)
        if agg.open_action_items:
            console.print(
                f"\n[bold]Open action items across series: {len(agg.open_action_items)}[/bold]"
            )
            for ai in agg.open_action_items[:10]:
                console.print(f"  ○ {ai['description'][:80]} (owner: {ai['owner'] or '—'})")
        if agg.recurring_topics:
            console.print(
                f"\n[bold]Recurring topics: {len(agg.recurring_topics)}[/bold]"
            )
            for t in agg.recurring_topics[:10]:
                console.print(f"  • {t['topic_summary'][:80]} (×{t['mention_count']})")
    finally:
        session.close()


# ---------------------------------------------------------------------------
# mm stats (ANA-1)
# ---------------------------------------------------------------------------


@stats_app.command("rebuild")
def stats_rebuild_cmd(
    min_similarity: float = typer.Option(0.8, "--min-similarity", help="Minimum cosine similarity to link chunks."),
):
    """Rebuild the topic-clusters cache for the ANA-1 Panel 2 view."""
    from meeting_minutes.stats_analytics import rebuild_topic_clusters_cache

    session = _get_db_session()
    try:
        result = rebuild_topic_clusters_cache(session, min_similarity=min_similarity)
    finally:
        session.close()

    if result.get("disabled_reason"):
        err_console.print(f"[yellow]{result['disabled_reason']}[/yellow]")
        raise typer.Exit(code=1)

    console.print(
        f"[green]Rebuilt topic clusters cache:[/green] "
        f"{result['cluster_count']} clusters across {result['chunk_count']} chunks."
    )


if __name__ == "__main__":
    app()
