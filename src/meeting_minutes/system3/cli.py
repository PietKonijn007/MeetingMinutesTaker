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
backup_app = typer.Typer(help="Database backup commands.")
service_app = typer.Typer(help="Manage the auto-start service (macOS).")

app.add_typer(record_app, name="record")
app.add_typer(actions_app, name="actions")
app.add_typer(backup_app, name="backup")
app.add_typer(service_app, name="service")

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
    auto_port: bool = typer.Option(True, "--auto-port/--no-auto-port", help="Auto-find a free port if the requested one is busy"),
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
        # Detect TTY — launchd/systemd/docker have no terminal, so prompts hang
        is_interactive = sys.stdin.isatty() and sys.stdout.isatty()

        if not is_interactive:
            console.print(f"[yellow]Port {port} is already in use (non-interactive — auto-resolving).[/yellow]")
            if not auto_port:
                err_console.print(f"[red]Port {port} in use and --no-auto-port is set.[/red]")
                raise typer.Exit(code=1)
            choice = "next"
        elif pid > 0:
            console.print(f"[yellow]Port {port} is already in use by PID {pid}.[/yellow]")
            choice = typer.prompt(
                "Kill the process and use this port, or find the next free port?",
                type=typer.Choice(["kill", "next", "abort"], case_sensitive=False),
                default="next",
            )
        else:
            console.print(f"[yellow]Port {port} is already in use.[/yellow]")
            choice = typer.prompt(
                "Find the next free port, or abort?",
                type=typer.Choice(["next", "abort"], case_sensitive=False),
                default="next",
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
        elif choice == "next":
            if not auto_port:
                err_console.print(f"[red]Port {port} in use and --no-auto-port is set.[/red]")
                raise typer.Exit(code=1)
            original = port
            for candidate in range(port + 1, port + 20):
                if _port_in_use(host, candidate) is None:
                    port = candidate
                    break
            else:
                err_console.print(f"[red]No free port found in range {original+1}–{original+19}[/red]")
                raise typer.Exit(code=1)
            console.print(f"  [green]Using port {port} instead[/green]")
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
    db_path = Path(config.storage.sqlite_path).expanduser()
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
    db_path = Path(config.storage.sqlite_path).expanduser()

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
    if result.stdout.strip():
        console.print("[yellow]Warning: You have uncommitted local changes.[/yellow]")
        console.print(result.stdout)
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

    # Switch to target branch if not already on it
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

    # 3. Install Python dependencies
    console.print("[bold][3/5] Updating Python dependencies...[/bold]")
    venv_pip = project_root / ".venv" / "bin" / "pip"
    if not venv_pip.exists():
        err_console.print("[red]Virtual environment not found. Run install.sh first.[/red]")
        raise typer.Exit(code=1)
    result = subprocess.run(
        [str(venv_pip), "install", "--quiet", "-e", ".[dev]"],
        capture_output=True, text=True, cwd=project_root,
    )
    if result.returncode != 0:
        err_console.print(f"[red]pip install failed: {result.stderr}[/red]")
        raise typer.Exit(code=1)
    console.print("  [green]✓[/green] Python dependencies updated")

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
        subprocess.run(["launchctl", "unload", str(PLIST_PATH)], capture_output=True)
        result = subprocess.run(["launchctl", "load", str(PLIST_PATH)], capture_output=True, text=True)
        if result.returncode == 0 or "already" in result.stderr.lower():
            console.print("  [green]✓[/green] Service restarted")
        else:
            err_console.print(f"  [yellow]Could not restart service: {result.stderr}[/yellow]")
    else:
        console.print("  [dim]Skipped (no service installed or --no-restart)[/dim]")

    console.print("\n[green bold]Upgrade complete![/green bold]")


if __name__ == "__main__":
    app()
