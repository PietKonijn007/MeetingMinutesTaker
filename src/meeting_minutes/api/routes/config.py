"""Config endpoints."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from meeting_minutes.api.deps import get_config
from meeting_minutes.api.schemas import ConfigResponse, ConfigUpdate
from meeting_minutes.api.secrets import (
    clear_secret,
    get_secret_status,
    is_valid_secret_name,
    set_secret,
)
from meeting_minutes.config import AppConfig, resolve_db_path

# ---------------------------------------------------------------------------
# Secret management — env-var values stored in .env (gitignored).
#
# Used by the settings UI to set things like PYANNOTEAI_API_KEY without
# requiring users to drop into a terminal. Values are write-only over the
# API: GET only reports whether a key is set and a sanitized preview.
# ---------------------------------------------------------------------------

# Whitelist of env-var names the UI is allowed to write. Keeps the surface
# narrow — random callers can't cram arbitrary names into .env.
_WRITABLE_SECRETS = {
    "PYANNOTEAI_API_KEY",
    "HF_TOKEN",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
}


class SecretBody(BaseModel):
    value: str = Field(..., min_length=1, max_length=4096)

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


def _validate_path_value(value: str, field: str) -> None:
    """Reject obviously-fragile path values from the settings UI.

    Both ``data_dir`` and ``storage.sqlite_path`` must be absolute (``/...``)
    or tilde-prefixed (``~/...``). Relative paths still load (for back-compat
    with older configs), but accepting them from the UI is a footgun: they
    resolve against the process cwd, which is rarely what the user intends.
    """
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(
            status_code=422,
            detail=f"{field} must be a non-empty string.",
        )
    stripped = value.strip()
    if not (stripped.startswith("/") or stripped.startswith("~")):
        raise HTTPException(
            status_code=422,
            detail=(
                f"{field} must be an absolute path (e.g. /Users/you/...) "
                f"or start with ~ (e.g. ~/MeetingMinutesTaker/...). "
                f"Got: {stripped!r}"
            ),
        )


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
    # Validate path-shaped fields before we even merge — gives the user a
    # clear, field-specific error instead of a generic Pydantic message.
    if "data_dir" in body.config:
        _validate_path_value(body.config["data_dir"], "data_dir")
    storage_patch = body.config.get("storage")
    if isinstance(storage_patch, dict) and "sqlite_path" in storage_patch:
        _validate_path_value(storage_patch["sqlite_path"], "storage.sqlite_path")

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


@router.get("/resolved-paths")
def get_resolved_paths(
    config: Annotated[AppConfig, Depends(get_config)],
):
    """Return the *expanded* absolute paths for the path-shaped config fields.

    The settings UI shows these underneath the editable input so the user can
    confirm that ``~/...`` (or a relative legacy value) actually points where
    they expect on this machine.
    """
    data_dir_raw = config.data_dir
    sqlite_raw = config.storage.sqlite_path
    return {
        "data_dir": {
            "raw": data_dir_raw,
            "resolved": str(Path(data_dir_raw).expanduser().resolve()),
        },
        "storage": {
            "sqlite_path": {
                "raw": sqlite_raw,
                "resolved": str(resolve_db_path(sqlite_raw).resolve()),
                "is_relative": not (
                    sqlite_raw.startswith("/") or sqlite_raw.startswith("~")
                ),
            }
        },
    }


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


@router.get("/secrets/{name}")
def get_secret_endpoint(name: str):
    """Return whether ``name`` is set in ``.env``, plus a sanitized preview.

    Never returns the value itself. The preview (first/last few chars + length)
    is enough for a user to confirm they pasted the right key without
    exposing it on the wire.
    """
    if not is_valid_secret_name(name) or name not in _WRITABLE_SECRETS:
        raise HTTPException(status_code=400, detail=f"Unknown or invalid secret: {name}")
    return get_secret_status(name)


@router.put("/secrets/{name}")
def set_secret_endpoint(name: str, body: SecretBody):
    """Write ``name=value`` to ``.env`` (gitignored).

    Restart required: env vars are loaded at process start, so an in-flight
    pyannote/openai client won't pick up the new value until the next server
    launch. The response includes ``restart_required: true`` so the UI can
    surface that to the user.
    """
    if not is_valid_secret_name(name) or name not in _WRITABLE_SECRETS:
        raise HTTPException(status_code=400, detail=f"Unknown or invalid secret: {name}")
    try:
        set_secret(name, body.value.strip())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"is_set": True, "restart_required": True}


@router.delete("/secrets/{name}")
def delete_secret_endpoint(name: str):
    """Remove ``name`` from ``.env``. Restart still required to take effect."""
    if not is_valid_secret_name(name) or name not in _WRITABLE_SECRETS:
        raise HTTPException(status_code=400, detail=f"Unknown or invalid secret: {name}")
    removed = clear_secret(name)
    return {"is_set": False, "removed": removed, "restart_required": removed}


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
