"""1:1 Copilot API endpoint."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from meeting_minutes.api.deps import get_config, get_db_session
from meeting_minutes.config import AppConfig
from meeting_minutes.copilot import (
    CopilotConfig,
    CopilotDossier,
    CopilotEngine,
)
from meeting_minutes.pipeline import resolve_user_name

router = APIRouter(prefix="/api/copilot", tags=["copilot"])


@router.get("/{person_id}", response_model=CopilotDossier)
async def get_copilot_dossier(
    person_id: str,
    focus: list[str] = Query(default=[]),
    session: Session = Depends(get_db_session),
    config: AppConfig = Depends(get_config),
) -> CopilotDossier:
    """Generate a 1:1 prep dossier for a person."""
    data_dir = Path(config.data_dir).expanduser()

    engine_config = CopilotConfig(
        your_name=config.copilot.your_name or resolve_user_name(config),
        lookback_days=config.copilot.lookback_days,
        sentiment_shift_threshold=config.copilot.sentiment_shift_threshold,
        talk_ratio_alert=config.copilot.talk_ratio_alert,
        llm_talking_points=config.copilot.llm_talking_points,
    )

    engine = CopilotEngine(session, data_dir, engine_config)
    dossier = engine.generate(person_id, focus=focus or None)

    if dossier.person_id == "unknown":
        raise HTTPException(status_code=404, detail=f"Person not found: {person_id}")

    return dossier
