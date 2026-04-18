"""Backup API endpoints."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from meeting_minutes.api.deps import get_config
from meeting_minutes.config import AppConfig, resolve_db_path

router = APIRouter(prefix="/api/backups", tags=["backups"])


class BackupResponse(BaseModel):
    filename: str
    path: str
    size_mb: float
    created: str


class BackupCreateResponse(BaseModel):
    filename: str
    path: str
    deleted_count: int


class ObsidianTestResponse(BaseModel):
    success: bool
    path: str = ""
    error: str = ""


@router.get("", response_model=list[BackupResponse])
def list_backups_endpoint(
    config: Annotated[AppConfig, Depends(get_config)],
):
    """List all available backups."""
    from meeting_minutes.backup import list_backups

    return list_backups(config.backup.backup_dir)


@router.post("", response_model=BackupCreateResponse)
def create_backup_endpoint(
    config: Annotated[AppConfig, Depends(get_config)],
):
    """Create a database backup now."""
    from meeting_minutes.backup import backup_database, rotate_backups

    db_path = resolve_db_path(config.storage.sqlite_path)
    if not db_path.exists():
        raise HTTPException(status_code=404, detail=f"Database not found: {db_path}")

    backup_dir = Path(config.backup.backup_dir)
    backup_file = backup_database(db_path, backup_dir)
    deleted = rotate_backups(backup_dir)

    return BackupCreateResponse(
        filename=backup_file.name,
        path=str(backup_file),
        deleted_count=deleted,
    )


@router.post("/obsidian-test", response_model=ObsidianTestResponse)
def test_obsidian_export(
    config: Annotated[AppConfig, Depends(get_config)],
):
    """Write a test note to the Obsidian vault to verify the path."""
    if not config.obsidian.vault_path:
        raise HTTPException(status_code=400, detail="Obsidian vault path not configured")

    from meeting_minutes.obsidian import export_to_obsidian

    vault_path = Path(config.obsidian.vault_path).expanduser()

    try:
        filepath = export_to_obsidian(
            vault_path=vault_path,
            title="Test Connection",
            date="2026-01-01",
            meeting_type="other",
            attendees=["Meeting Minutes Taker"],
            minutes_markdown="This is a test note from Meeting Minutes Taker.\n\nIf you can see this, the Obsidian vault connection is working.",
            meeting_id="test",
        )
        return ObsidianTestResponse(success=True, path=str(filepath))
    except Exception as e:
        return ObsidianTestResponse(success=False, error=str(e))
