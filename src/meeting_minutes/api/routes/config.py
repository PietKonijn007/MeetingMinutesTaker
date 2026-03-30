"""Config endpoints."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import yaml
from fastapi import APIRouter, Depends, HTTPException

from meeting_minutes.api.deps import get_config
from meeting_minutes.api.schemas import ConfigResponse, ConfigUpdate
from meeting_minutes.config import AppConfig

router = APIRouter(prefix="/api/config", tags=["config"])


def _find_config_path() -> Path:
    """Return the path to the config YAML file (first found or default)."""
    candidates = [
        Path("config/config.yaml"),
        Path.home() / ".meeting-minutes" / "config.yaml",
    ]
    for p in candidates:
        if p.exists():
            return p
    # Default to ./config/config.yaml for writing
    return candidates[0]


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base (returns a new dict)."""
    merged = dict(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


@router.get("", response_model=ConfigResponse)
def get_current_config(
    config: Annotated[AppConfig, Depends(get_config)],
):
    """Get the current configuration as JSON."""
    return ConfigResponse(config=config.model_dump())


@router.patch("", response_model=ConfigResponse)
def update_config(
    body: ConfigUpdate,
):
    """Merge-update the configuration and write to YAML."""
    config_path = _find_config_path()

    # Load existing YAML (or empty dict)
    existing: dict = {}
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            existing = yaml.safe_load(f) or {}

    # Merge
    merged = _deep_merge(existing, body.config)

    # Validate that the merged config is still valid
    try:
        new_config = AppConfig(**merged)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid configuration: {exc}")

    # Write back
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(merged, f, default_flow_style=False, sort_keys=False)

    return ConfigResponse(config=new_config.model_dump())
