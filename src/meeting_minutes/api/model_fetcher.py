"""Fetch available models from LLM provider APIs with disk caching."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

CACHE_PATH = Path(__file__).parent.parent.parent.parent / "config" / "model_cache.json"
CACHE_TTL_SECONDS = 24 * 60 * 60  # 24 hours

# Hardcoded fallbacks when API is unreachable or key is missing
_FALLBACK_MODELS: dict[str, list[dict[str, Any]]] = {
    "anthropic": [
        {"id": "claude-opus-4-6", "name": "Claude Opus 4.6"},
        {"id": "claude-sonnet-4-6", "name": "Claude Sonnet 4.6"},
        {"id": "claude-haiku-4-5-20251001", "name": "Claude Haiku 4.5"},
    ],
    "openai": [
        {"id": "gpt-4o", "name": "GPT-4o"},
        {"id": "gpt-4o-mini", "name": "GPT-4o Mini"},
    ],
    "openrouter": [
        {"id": "anthropic/claude-sonnet-4", "name": "Claude Sonnet 4", "pricing": {"prompt": "3.00", "completion": "15.00"}},
        {"id": "anthropic/claude-haiku-4", "name": "Claude Haiku 4", "pricing": {"prompt": "0.80", "completion": "4.00"}},
        {"id": "google/gemini-2.5-pro-preview", "name": "Gemini 2.5 Pro", "pricing": {"prompt": "2.50", "completion": "15.00"}},
        {"id": "google/gemini-2.5-flash-preview", "name": "Gemini 2.5 Flash", "pricing": {"prompt": "0.15", "completion": "0.60"}},
        {"id": "openai/gpt-4o", "name": "GPT-4o", "pricing": {"prompt": "2.50", "completion": "10.00"}},
        {"id": "openai/gpt-4o-mini", "name": "GPT-4o Mini", "pricing": {"prompt": "0.15", "completion": "0.60"}},
        {"id": "meta-llama/llama-4-maverick", "name": "Llama 4 Maverick", "pricing": {"prompt": "0.50", "completion": "1.50"}},
        {"id": "deepseek/deepseek-r1", "name": "DeepSeek R1", "pricing": {"prompt": "0.55", "completion": "2.19"}},
        {"id": "mistralai/mistral-medium-3", "name": "Mistral Medium 3", "pricing": {"prompt": "2.00", "completion": "5.00"}},
    ],
    "ollama": [],
}


def _read_cache() -> dict[str, Any]:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text())
        except Exception:
            pass
    return {}


def _write_cache(cache: dict[str, Any]) -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(cache, indent=2) + "\n")
    except Exception:
        pass


def _is_cache_fresh(cache: dict, provider: str) -> bool:
    entry = cache.get(provider)
    if not entry or "fetched_at" not in entry:
        return False
    try:
        fetched = datetime.fromisoformat(entry["fetched_at"])
        age = (datetime.now(timezone.utc) - fetched).total_seconds()
        return age < CACHE_TTL_SECONDS
    except Exception:
        return False


async def _fetch_anthropic() -> list[dict[str, Any]]:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return _FALLBACK_MODELS["anthropic"]

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            "https://api.anthropic.com/v1/models",
            params={"limit": 1000},
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    models = []
    for m in data.get("data", []):
        model_id = m.get("id", "")
        # Skip embedding models, legacy models without display_name
        display_name = m.get("display_name", model_id)
        models.append({
            "id": model_id,
            "name": display_name,
            "context_length": m.get("max_input_tokens"),
        })

    # Sort by name
    models.sort(key=lambda x: x["name"])
    return models


async def _fetch_openrouter() -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get("https://openrouter.ai/api/v1/models")
        resp.raise_for_status()
        data = resp.json()

    models = []
    for m in data.get("data", []):
        model_id = m.get("id", "")
        name = m.get("name", model_id)
        pricing = m.get("pricing", {})
        context_length = m.get("context_length")

        # Convert per-token pricing to per-million-tokens for display
        pricing_display = None
        try:
            prompt_per_token = float(pricing.get("prompt", "0"))
            completion_per_token = float(pricing.get("completion", "0"))
            if prompt_per_token > 0 or completion_per_token > 0:
                pricing_display = {
                    "prompt": f"{prompt_per_token * 1_000_000:.2f}",
                    "completion": f"{completion_per_token * 1_000_000:.2f}",
                }
        except (ValueError, TypeError):
            pass

        entry: dict[str, Any] = {
            "id": model_id,
            "name": name,
        }
        if context_length:
            entry["context_length"] = context_length
        if pricing_display:
            entry["pricing"] = pricing_display

        models.append(entry)

    # Sort by provider prefix then name
    models.sort(key=lambda x: x["id"])
    return models


async def _fetch_openai() -> list[dict[str, Any]]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return _FALLBACK_MODELS["openai"]

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        resp.raise_for_status()
        data = resp.json()

    # Filter to only chat-relevant models
    _CHAT_PREFIXES = ("gpt-4", "gpt-3.5", "o1", "o3", "o4")
    _EXCLUDE = ("instruct", "realtime", "audio", "search")

    models = []
    for m in data.get("data", []):
        model_id = m.get("id", "")
        if not any(model_id.startswith(p) for p in _CHAT_PREFIXES):
            continue
        if any(ex in model_id for ex in _EXCLUDE):
            continue
        models.append({
            "id": model_id,
            "name": model_id,
        })

    models.sort(key=lambda x: x["id"])
    return models


async def _fetch_ollama() -> list[dict[str, Any]]:
    """Fetch available models from a local Ollama instance."""
    base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

    async with httpx.AsyncClient(timeout=5) as client:
        resp = await client.get(f"{base_url}/api/tags")
        resp.raise_for_status()
        data = resp.json()

    models = []
    for m in data.get("models", []):
        model_name = m.get("name", "")
        size_bytes = m.get("size", 0)
        size_gb = size_bytes / (1024 ** 3) if size_bytes else None
        parameter_size = m.get("details", {}).get("parameter_size", "")
        family = m.get("details", {}).get("family", "")
        quantization = m.get("details", {}).get("quantization_level", "")

        entry: dict[str, Any] = {
            "id": model_name,
            "name": model_name,
        }
        if size_gb:
            entry["size_gb"] = round(size_gb, 1)
        if parameter_size:
            entry["parameter_size"] = parameter_size
        if family:
            entry["family"] = family
        if quantization:
            entry["quantization"] = quantization

        models.append(entry)

    models.sort(key=lambda x: x["name"])
    return models


_FETCHERS = {
    "anthropic": _fetch_anthropic,
    "openrouter": _fetch_openrouter,
    "openai": _fetch_openai,
    "ollama": _fetch_ollama,
}


async def get_provider_models(
    provider: str,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Return available models for a provider. Uses disk cache with 24h TTL."""
    if provider not in _FETCHERS:
        return {
            "provider": provider,
            "models": _FALLBACK_MODELS.get(provider, []),
            "cached": False,
            "source": "fallback",
        }

    cache = _read_cache()

    # Return cached if fresh and not forcing refresh
    if not force_refresh and _is_cache_fresh(cache, provider):
        return {
            "provider": provider,
            "models": cache[provider]["models"],
            "cached": True,
            "source": "cache",
        }

    # Fetch from API
    try:
        models = await _FETCHERS[provider]()
        # Update cache
        cache[provider] = {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "models": models,
        }
        _write_cache(cache)
        return {
            "provider": provider,
            "models": models,
            "cached": False,
            "source": "api",
        }
    except Exception as exc:
        # Try stale cache first
        if provider in cache and "models" in cache[provider]:
            return {
                "provider": provider,
                "models": cache[provider]["models"],
                "cached": True,
                "source": "stale_cache",
                "warning": f"API fetch failed: {exc}",
            }
        # Fall back to hardcoded
        return {
            "provider": provider,
            "models": _FALLBACK_MODELS.get(provider, []),
            "cached": False,
            "source": "fallback",
            "warning": f"API fetch failed: {exc}",
        }
