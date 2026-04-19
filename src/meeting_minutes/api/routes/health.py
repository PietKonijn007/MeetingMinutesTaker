"""Health check endpoints (HLT-1)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from meeting_minutes.api.deps import get_config, get_db_session
from meeting_minutes.config import AppConfig

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("/full")
def health_full(
    session: Annotated[Session, Depends(get_db_session)],
    config: Annotated[AppConfig, Depends(get_config)],
):
    """Run all HLT-1 checks and return the structured report.

    Slow compared to a liveness probe — do not use this from load
    balancers. Intended for the UI banner and ``mm repair``.
    """
    from meeting_minutes.health import check_all

    report = check_all(session, config)
    return report.to_dict()
