"""Config endpoints."""

from __future__ import annotations

import json
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


@router.get("/custom-models")
def get_custom_models():
    """Return custom models that have been successfully used, keyed by provider."""
    custom_models_path = Path(__file__).parent.parent.parent.parent.parent / "config" / "custom_models.json"
    if custom_models_path.exists():
        try:
            return json.loads(custom_models_path.read_text())
        except Exception:
            pass
    return {"anthropic": [], "openai": [], "openrouter": [], "ollama": []}


@router.get("/provider-models")
async def get_provider_models_endpoint(
    provider: str,
    refresh: bool = False,
):
    """Fetch available models for a provider from its API. Cached for 24h."""
    valid_providers = ("anthropic", "openai", "openrouter", "ollama")
    if provider not in valid_providers:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown provider: {provider}. Must be one of: {', '.join(valid_providers)}",
        )

    from meeting_minutes.api.model_fetcher import get_provider_models

    return await get_provider_models(provider, force_refresh=refresh)


@router.get("/transcription-engines")
def get_transcription_engines():
    """List available transcription engines and their install status."""
    from meeting_minutes.system1.transcribe import get_available_engines, WHISPER_PRESETS

    return {
        "engines": get_available_engines(),
        "presets": WHISPER_PRESETS,
    }


@router.get("/hardware")
def get_hardware_info():
    """Detect hardware capabilities and recommend models for local AI."""
    from meeting_minutes.hardware import detect_hardware, recommend_models, check_ollama_available

    profile = detect_hardware()
    recommendations = recommend_models(profile)
    ollama_status = check_ollama_available()

    return {
        "hardware": {
            "gpu": {
                "name": profile.gpu.name,
                "type": profile.gpu.type,
                "vram_gb": round(profile.gpu.vram_gb, 1),
            },
            "ram_gb": round(profile.total_ram_gb, 1),
            "available_ram_gb": round(profile.available_ram_gb, 1),
            "cpu_cores": profile.cpu_cores,
            "platform": profile.platform,
            "arch": profile.arch,
        },
        "recommendations": {
            "whisper_model": recommendations.whisper_model,
            "whisper_device": recommendations.whisper_device,
            "whisper_compute_type": recommendations.whisper_compute_type,
            "ollama_models": recommendations.ollama_models,
            "max_ollama_model_gb": round(recommendations.max_ollama_model_gb, 1),
            "notes": recommendations.notes,
        },
        "ollama": ollama_status,
    }
