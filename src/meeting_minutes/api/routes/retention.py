"""Retention policy API endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from meeting_minutes.api.deps import get_config
from meeting_minutes.config import AppConfig

router = APIRouter(prefix="/api/retention", tags=["retention"])


@router.post("/cleanup")
def run_retention_cleanup(
    config: Annotated[AppConfig, Depends(get_config)],
):
    """Enforce retention policies — delete old files."""
    from meeting_minutes.retention import enforce_retention

    deleted = enforce_retention(config)
    return {"deleted": deleted, "total": sum(deleted.values())}


@router.get("/status")
def retention_status(
    config: Annotated[AppConfig, Depends(get_config)],
):
    """Return current retention config, file counts, and oldest file ages."""
    from meeting_minutes.retention import get_retention_status

    return get_retention_status(config)
