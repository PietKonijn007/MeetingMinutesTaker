"""LLM API client with multi-provider support (Anthropic, OpenAI, OpenRouter, Ollama, OpenAI-compatible local)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from typing import Optional

from meeting_minutes.config import LLMConfig
from meeting_minutes.models import LLMResponse

logger = logging.getLogger(__name__)


class ContextWindowExceededError(RuntimeError):
    """Raised when a prompt is too large for the model's context window."""


# Cost per 1K tokens (approximate, as of early 2025)
COST_PER_1K_TOKENS: dict[str, dict[str, float]] = {
    "anthropic": {
        "claude-opus-4-6": 0.015,
        "claude-sonnet-4-6": 0.003,
        "claude-haiku-4-5-20251001": 0.0008,
        "default": 0.003,
    },
    "openai": {
        "gpt-4o": 0.005,
        "gpt-4o-mini": 0.00015,
        "default": 0.005,
    },
    "openrouter": {
        # Pricing varies per model; these are rough estimates per 1K tokens
        "anthropic/claude-sonnet-4": 0.003,
        "anthropic/claude-haiku-4": 0.0008,
        "google/gemini-2.5-pro-preview": 0.0025,
        "google/gemini-2.5-flash-preview": 0.0003,
        "openai/gpt-4o": 0.005,
        "openai/gpt-4o-mini": 0.00015,
        "meta-llama/llama-4-maverick": 0.0005,
        "deepseek/deepseek-r1": 0.0008,
        "mistralai/mistral-medium-3": 0.002,
        "default": 0.003,
    },
    "ollama": {
        "default": 0.0,  # Local models are free
    },
    "openai_compatible": {
        "default": 0.0,  # Local / self-hosted — no per-token cost
    },
}


def _calculate_cost(provider: str, model: str, input_tokens: int, output_tokens: int) -> float:
    provider_costs = COST_PER_1K_TOKENS.get(provider, {})
    rate = provider_costs.get(model, provider_costs.get("default", 0.003))
    return (input_tokens + output_tokens) / 1000 * rate


_ARRAY_TO_DICT_KEYS: dict[str, list[str]] = {
    "participants": ["name", "role", "sentiment"],
    "discussion_points": ["topic", "summary", "participants", "sentiment"],
    "decisions": ["description", "made_by", "rationale", "confidence"],
    "risks_and_concerns": ["description", "raised_by"],
    "follow_ups": ["description", "owner", "timeframe"],
    "open_questions": ["question", "raised_by", "owner"],
    "action_items": ["description", "owner", "due_date", "priority"],
}

_ACTION_ITEM_DESC_ALIASES = ("action", "task", "item", "text", "title", "name", "content")

# Map of <list field> -> {<required key>: (<alias>, ...)}.
# When the model returns a dict item missing the required key, copy from the
# first matching alias. Keeps JSON-mode output compatible with the strict
# Pydantic schema without changing the schema itself.
_LIST_ITEM_KEY_ALIASES: dict[str, dict[str, tuple[str, ...]]] = {
    "discussion_points": {
        "summary": ("description", "details", "text", "content", "notes", "summary_text"),
        "topic": ("title", "name", "subject", "heading"),
    },
    "decisions": {
        "description": ("decision", "text", "summary", "content", "title"),
    },
    "risks_and_concerns": {
        "description": ("risk", "concern", "text", "summary", "content", "title"),
    },
    "follow_ups": {
        "description": ("follow_up", "task", "action", "text", "summary", "content", "title"),
    },
    "open_questions": {
        "question": ("text", "summary", "content", "title"),
    },
    "action_items": {
        "description": _ACTION_ITEM_DESC_ALIASES,
    },
}


def _normalize_structured_response(data: dict) -> dict:
    """Fix common schema mismatches from local models.

    Handles: arrays-as-objects, missing/renamed fields, strings-as-lists.
    """
    if not isinstance(data, dict):
        return data

    for field, keys in _ARRAY_TO_DICT_KEYS.items():
        items = data.get(field)
        if not isinstance(items, list):
            continue
        normalized = []
        for item in items:
            if isinstance(item, dict):
                normalized.append(item)
            elif isinstance(item, (list, tuple)):
                entry = {}
                for i, val in enumerate(item):
                    if i < len(keys):
                        entry[keys[i]] = val
                normalized.append(entry)
            elif isinstance(item, str):
                normalized.append({keys[0]: item})
            else:
                normalized.append(item)
        data[field] = normalized

    # Apply per-list alias rewrites so items satisfy required schema keys.
    for field, alias_map in _LIST_ITEM_KEY_ALIASES.items():
        items = data.get(field)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            for required_key, aliases in alias_map.items():
                if required_key in item and item[required_key] not in (None, ""):
                    continue
                for alias in aliases:
                    if alias in item and item[alias] not in (None, ""):
                        item[required_key] = item.pop(alias)
                        break

    email = data.get("email_draft")
    if isinstance(email, str):
        # Some models return the email as a plain text body.
        data["email_draft"] = {"body": email}
        email = data["email_draft"]
    elif isinstance(email, list):
        # Or as a [subject, body] / [body] sequence.
        if len(email) >= 2:
            data["email_draft"] = {"subject": email[0], "body": email[1]}
        elif len(email) == 1:
            data["email_draft"] = {"body": email[0]}
        else:
            data["email_draft"] = {}
        email = data["email_draft"]
    if isinstance(email, dict):
        for list_field in ("to", "cc"):
            val = email.get(list_field)
            if isinstance(val, str):
                email[list_field] = [v.strip() for v in val.split(",") if v.strip()]

    effectiveness = data.get("meeting_effectiveness")
    if isinstance(effectiveness, (list, tuple)):
        eff_keys = ["had_clear_agenda", "decisions_made", "action_items_assigned", "unresolved_items"]
        entry = {}
        for i, val in enumerate(effectiveness):
            if i < len(eff_keys):
                entry[eff_keys[i]] = val
        data["meeting_effectiveness"] = entry
    elif isinstance(effectiveness, str):
        # Reduce a free-text rating to an empty dict; downstream defaults apply.
        data["meeting_effectiveness"] = {}

    return data


class LLMClient:
    """Send prompts to LLM and return raw responses. Supports Anthropic, OpenAI, OpenRouter, and Ollama."""

    def __init__(self, config: LLMConfig) -> None:
        self._config = config
        self._model_context_length: int | None = None

    async def _fetch_model_context_length(self) -> int | None:
        """Query the provider's /models endpoint for the selected model's context length."""
        provider = self._config.primary_provider
        model = self._config.model

        if provider not in ("ollama", "openai_compatible"):
            return None

        try:
            from meeting_minutes.api.model_fetcher import get_provider_models
            result = await get_provider_models(provider)
            for m in result.get("models", []):
                if m.get("id") == model and m.get("context_length"):
                    return m["context_length"]
        except Exception:
            logger.debug("Could not fetch context length for %s/%s", provider, model)

        return None

    async def check_context_fits(
        self, prompt: str, system_prompt: str = ""
    ) -> None:
        """Raise if the prompt likely exceeds the model's context window.

        Uses a rough 4-chars-per-token estimate. Only checks local providers
        (ollama, openai_compatible) where the context window is user-configured
        and often too small.
        """
        if self._config.primary_provider not in ("ollama", "openai_compatible"):
            return

        if self._model_context_length is None:
            self._model_context_length = await self._fetch_model_context_length()

        ctx = self._model_context_length
        if ctx is None:
            return

        estimated_tokens = (len(prompt) + len(system_prompt)) // 4
        available_for_output = ctx - estimated_tokens

        if available_for_output < self._config.max_output_tokens:
            ctx_k = f"{ctx // 1024}K" if ctx >= 1024 else str(ctx)
            raise ContextWindowExceededError(
                f"Prompt is too long for the model's context window. "
                f"Estimated prompt tokens: ~{estimated_tokens:,}, "
                f"model context: {ctx_k} ({ctx:,} tokens), "
                f"needed for output: {self._config.max_output_tokens:,}, "
                f"shortfall: ~{self._config.max_output_tokens - available_for_output:,} tokens. "
                f"Increase the model's context length in your local server "
                f"(e.g. LM Studio → Load tab → Context Length) to at least "
                f"{estimated_tokens + self._config.max_output_tokens:,} tokens."
            )

    def _fallback_retry_attempts(self) -> int:
        return self._config.fallback_retry_attempts if self._config.fallback_retry_attempts is not None else self._config.retry_attempts

    @contextlib.contextmanager
    def _fallback_overrides(self):
        """Temporarily apply fallback-specific settings to self._config."""
        originals = {}
        overrides = {
            "temperature": self._config.fallback_temperature,
            "max_output_tokens": self._config.fallback_max_output_tokens,
            "timeout_seconds": self._config.fallback_timeout_seconds,
        }
        for attr, val in overrides.items():
            if val is not None:
                originals[attr] = getattr(self._config, attr)
                object.__setattr__(self._config, attr, val)
        try:
            yield
        finally:
            for attr, val in originals.items():
                object.__setattr__(self._config, attr, val)

    async def _try_with_retries(
        self,
        provider: str,
        model: str,
        prompt: str,
        system_prompt: str,
        retry_attempts: int,
    ) -> LLMResponse:
        for attempt in range(retry_attempts):
            try:
                return await self._generate_with_provider(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    provider=provider,
                    model=model,
                )
            except Exception as exc:
                if attempt < retry_attempts - 1:
                    wait = 2 ** attempt
                    logger.warning(
                        "LLM call to %s/%s failed (attempt %d/%d): %s — retrying in %ds",
                        provider, model, attempt + 1, retry_attempts, exc, wait,
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.error(
                        "LLM call to %s/%s failed after %d attempts: %s",
                        provider, model, retry_attempts, exc,
                    )
                    raise

    async def generate(self, prompt: str, system_prompt: str = "") -> LLMResponse:
        """Send prompt to configured LLM provider. Returns response with token usage."""
        try:
            return await self._try_with_retries(
                provider=self._config.primary_provider,
                model=self._config.model,
                prompt=prompt,
                system_prompt=system_prompt,
                retry_attempts=self._config.retry_attempts,
            )
        except Exception as exc:
            if self._config.fallback_provider and self._config.fallback_model:
                logger.warning(
                    "Primary provider %s/%s failed — switching to fallback provider %s/%s",
                    self._config.primary_provider, self._config.model,
                    self._config.fallback_provider, self._config.fallback_model,
                )
                try:
                    with self._fallback_overrides():
                        response = await self._try_with_retries(
                            provider=self._config.fallback_provider,
                            model=self._config.fallback_model,
                            prompt=prompt,
                            system_prompt=system_prompt,
                            retry_attempts=self._fallback_retry_attempts(),
                        )
                    logger.info(
                        "Fallback provider %s/%s succeeded",
                        self._config.fallback_provider, self._config.fallback_model,
                    )
                    return response
                except Exception as fallback_exc:
                    logger.error(
                        "Fallback provider %s/%s also failed: %s",
                        self._config.fallback_provider, self._config.fallback_model, fallback_exc,
                    )
                    raise RuntimeError(
                        f"Both primary ({self._config.primary_provider}) and fallback "
                        f"({self._config.fallback_provider}) providers failed. "
                        f"Primary: {exc}; Fallback: {fallback_exc}"
                    ) from fallback_exc
            raise RuntimeError(
                f"LLM generation failed after {self._config.retry_attempts} attempts: {exc}"
            ) from exc

    async def _generate_with_provider(
        self, prompt: str, system_prompt: str, provider: str, model: str
    ) -> LLMResponse:
        start = time.time()

        if provider == "anthropic":
            text, input_tokens, output_tokens = await self._call_anthropic(
                prompt, system_prompt, model
            )
        elif provider == "openai":
            text, input_tokens, output_tokens = await self._call_openai(
                prompt, system_prompt, model
            )
        elif provider == "openrouter":
            text, input_tokens, output_tokens = await self._call_openrouter(
                prompt, system_prompt, model
            )
        elif provider == "ollama":
            text, input_tokens, output_tokens = await self._call_ollama(
                prompt, system_prompt, model
            )
        elif provider == "openai_compatible":
            text, input_tokens, output_tokens = await self._call_openai_compatible_local(
                prompt, system_prompt, model
            )
        else:
            raise ValueError(f"Unknown provider: {provider}")

        elapsed = time.time() - start
        cost = _calculate_cost(provider, model, input_tokens, output_tokens)

        return LLMResponse(
            text=text,
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            processing_time_seconds=elapsed,
        )

    async def _call_anthropic(
        self, prompt: str, system_prompt: str, model: str
    ) -> tuple[str, int, int]:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY environment variable not set")

        try:
            import anthropic  # lazy import
        except ImportError as exc:
            raise RuntimeError("anthropic package not installed") from exc

        client = anthropic.AsyncAnthropic(api_key=api_key)

        messages = [{"role": "user", "content": prompt}]

        kwargs: dict = dict(
            model=model,
            max_tokens=self._config.max_output_tokens,
            system=system_prompt if system_prompt else "You are a helpful assistant.",
            messages=messages,
        )
        if not model.startswith("claude-opus"):
            kwargs["temperature"] = self._config.temperature

        # Use streaming to avoid the SDK's 10-minute non-streaming timeout cap
        # that trips whenever max_tokens would make the request potentially
        # exceed 10 minutes (any max_tokens > ~21k triggers it). We still want
        # the full assembled message, so collect it via get_final_message().
        async with client.messages.stream(**kwargs) as stream:
            response = await stream.get_final_message()

        text = response.content[0].text if response.content else ""
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens

        return text, input_tokens, output_tokens

    async def _try_structured_with_retries(
        self,
        provider: str,
        model: str,
        prompt: str,
        system_prompt: str,
        tool_definition: dict,
        retry_attempts: int,
    ) -> LLMResponse:
        use_anthropic_native = (provider == "anthropic")
        for attempt in range(retry_attempts):
            try:
                if use_anthropic_native:
                    return await self._call_anthropic_structured(
                        prompt=prompt,
                        system_prompt=system_prompt,
                        model=model,
                        tool_definition=tool_definition,
                    )
                else:
                    return await self._generate_structured_via_json(
                        prompt=prompt,
                        system_prompt=system_prompt,
                        provider=provider,
                        model=model,
                        tool_definition=tool_definition,
                    )
            except Exception as exc:
                if attempt < retry_attempts - 1:
                    wait = 2 ** attempt
                    logger.warning(
                        "Structured LLM call to %s/%s failed (attempt %d/%d): %s — retrying in %ds",
                        provider, model, attempt + 1, retry_attempts, exc, wait,
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.error(
                        "Structured LLM call to %s/%s failed after %d attempts: %s",
                        provider, model, retry_attempts, exc,
                    )
                    raise

    async def generate_structured(
        self, prompt: str, system_prompt: str = "", tool_definition: dict | None = None
    ) -> LLMResponse:
        """Generate structured output. Uses Anthropic tool_use when available, JSON mode for others."""
        if tool_definition is None:
            from meeting_minutes.system2.schema import get_tool_definition
            tool_definition = get_tool_definition()

        try:
            return await self._try_structured_with_retries(
                provider=self._config.primary_provider,
                model=self._config.model,
                prompt=prompt,
                system_prompt=system_prompt,
                tool_definition=tool_definition,
                retry_attempts=self._config.retry_attempts,
            )
        except Exception as exc:
            if self._config.fallback_provider and self._config.fallback_model:
                logger.warning(
                    "Primary provider %s/%s failed structured generation — switching to fallback provider %s/%s",
                    self._config.primary_provider, self._config.model,
                    self._config.fallback_provider, self._config.fallback_model,
                )
                try:
                    with self._fallback_overrides():
                        response = await self._try_structured_with_retries(
                            provider=self._config.fallback_provider,
                            model=self._config.fallback_model,
                            prompt=prompt,
                            system_prompt=system_prompt,
                            tool_definition=tool_definition,
                            retry_attempts=self._fallback_retry_attempts(),
                        )
                    logger.info(
                        "Fallback provider %s/%s succeeded (structured generation)",
                        self._config.fallback_provider, self._config.fallback_model,
                    )
                    return response
                except Exception as fallback_exc:
                    logger.error(
                        "Fallback provider %s/%s also failed structured generation: %s",
                        self._config.fallback_provider, self._config.fallback_model, fallback_exc,
                    )
                    raise RuntimeError(
                        f"Both primary ({self._config.primary_provider}) and fallback "
                        f"({self._config.fallback_provider}) providers failed structured generation. "
                        f"Primary: {exc}; Fallback: {fallback_exc}"
                    ) from fallback_exc
            raise RuntimeError(
                f"Structured generation failed after {self._config.retry_attempts} attempts: {exc}"
            ) from exc

    async def _call_anthropic_structured(
        self, prompt: str, system_prompt: str, model: str, tool_definition: dict
    ) -> LLMResponse:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY environment variable not set")

        try:
            import anthropic
        except ImportError as exc:
            raise RuntimeError("anthropic package not installed") from exc

        client = anthropic.AsyncAnthropic(api_key=api_key)

        start = time.time()
        kwargs: dict = dict(
            model=model,
            max_tokens=self._config.max_output_tokens,
            system=system_prompt if system_prompt else "You are a helpful assistant.",
            messages=[{"role": "user", "content": prompt}],
            tools=[tool_definition],
            tool_choice={"type": "tool", "name": tool_definition["name"]},
        )
        if not model.startswith("claude-opus"):
            kwargs["temperature"] = self._config.temperature

        # Stream to bypass the SDK's non-streaming 10-minute timeout guard
        # (see _call_anthropic for details).
        async with client.messages.stream(**kwargs) as stream:
            response = await stream.get_final_message()
        elapsed = time.time() - start

        # Extract structured data from tool_use block
        structured_data = None
        preamble_text = ""
        for block in response.content:
            if block.type == "tool_use":
                structured_data = block.input
            elif block.type == "text":
                preamble_text += block.text

        if structured_data is None:
            raise RuntimeError("LLM did not return a tool_use block")

        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        cost = _calculate_cost("anthropic", model, input_tokens, output_tokens)

        return LLMResponse(
            text=preamble_text,
            provider="anthropic",
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            processing_time_seconds=elapsed,
            structured_data=structured_data,
        )

    async def _call_openai(
        self, prompt: str, system_prompt: str, model: str
    ) -> tuple[str, int, int]:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY environment variable not set")

        try:
            from openai import AsyncOpenAI  # lazy import
        except ImportError as exc:
            raise RuntimeError("openai package not installed") from exc

        client = AsyncOpenAI(api_key=api_key)

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        response = await client.chat.completions.create(
            model=model,
            max_tokens=self._config.max_output_tokens,
            temperature=self._config.temperature,
            messages=messages,
        )

        text = response.choices[0].message.content or ""
        input_tokens = response.usage.prompt_tokens
        output_tokens = response.usage.completion_tokens

        return text, input_tokens, output_tokens

    async def _call_openrouter(
        self, prompt: str, system_prompt: str, model: str
    ) -> tuple[str, int, int]:
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise RuntimeError("OPENROUTER_API_KEY environment variable not set")

        try:
            from openai import AsyncOpenAI  # OpenRouter uses OpenAI-compatible API
        except ImportError as exc:
            raise RuntimeError("openai package not installed (needed for OpenRouter)") from exc

        client = AsyncOpenAI(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
        )

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        response = await client.chat.completions.create(
            model=model,
            max_tokens=self._config.max_output_tokens,
            temperature=self._config.temperature,
            messages=messages,
        )

        text = response.choices[0].message.content or ""
        input_tokens = response.usage.prompt_tokens if response.usage else 0
        output_tokens = response.usage.completion_tokens if response.usage else 0

        return text, input_tokens, output_tokens

    async def _call_openai_compatible(
        self,
        prompt: str,
        system_prompt: str,
        model: str,
        base_url: str,
        api_key: str,
        provider_label: str,
    ) -> tuple[str, int, int]:
        """Shared transport for any OpenAI-compatible chat-completions endpoint.

        Used by Ollama (which exposes ``/v1`` on top of its native API) and
        the generic ``openai_compatible`` provider (LM Studio, vLLM,
        llama.cpp's server, etc.). ``base_url`` should already include the
        ``/v1`` segment if the server requires it.
        """
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise RuntimeError("openai package not installed") from exc

        client = AsyncOpenAI(api_key=api_key, base_url=base_url)

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        try:
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=self._config.temperature,
                max_tokens=self._config.max_output_tokens,
            )
        except Exception as exc:
            raise RuntimeError(
                f"{provider_label} request failed at {base_url}: {exc}"
            ) from exc

        text = response.choices[0].message.content or ""
        input_tokens = response.usage.prompt_tokens if response.usage else 0
        output_tokens = response.usage.completion_tokens if response.usage else 0

        return text, input_tokens, output_tokens

    async def _call_ollama(
        self, prompt: str, system_prompt: str, model: str
    ) -> tuple[str, int, int]:
        """Call a local Ollama instance via its OpenAI-compatible API."""
        config_url = getattr(self._config, "ollama", None)
        default_url = config_url.base_url if config_url else "http://localhost:11434"
        base = os.environ.get("OLLAMA_BASE_URL", default_url).rstrip("/")
        return await self._call_openai_compatible(
            prompt,
            system_prompt,
            model,
            base_url=f"{base}/v1",
            api_key="ollama",  # Ollama doesn't require a real key
            provider_label="Ollama",
        )

    async def _call_openai_compatible_local(
        self, prompt: str, system_prompt: str, model: str
    ) -> tuple[str, int, int]:
        """Call a user-configured OpenAI-compatible local server.

        Covers LM Studio, vLLM, llama.cpp's HTTP server, text-generation-webui's
        OpenAI extension, and similar — anything that speaks the OpenAI
        chat-completions wire format. The base URL is taken from
        ``generation.llm.openai_compatible.base_url`` (override with
        ``MM_OPENAI_COMPATIBLE_BASE_URL``).
        """
        cfg = getattr(self._config, "openai_compatible", None)
        default_url = cfg.base_url if cfg else "http://localhost:1234/v1"
        base_url = os.environ.get("MM_OPENAI_COMPATIBLE_BASE_URL", default_url)

        api_key = "not-needed"
        if cfg and cfg.api_key_env:
            api_key = os.environ.get(cfg.api_key_env) or "not-needed"

        return await self._call_openai_compatible(
            prompt,
            system_prompt,
            model,
            base_url=base_url,
            api_key=api_key,
            provider_label="OpenAI-compatible local",
        )

    async def _generate_structured_via_json(
        self,
        prompt: str,
        system_prompt: str,
        provider: str,
        model: str,
        tool_definition: dict,
    ) -> LLMResponse:
        """Generate structured output by requesting JSON from non-Anthropic providers.

        Wraps the tool_definition schema into the prompt and asks the model to
        respond with valid JSON matching that schema.
        """
        import json

        # Build a JSON-focused system prompt
        schema = tool_definition.get("input_schema", {})
        properties = schema.get("properties", {})
        field_descriptions = []
        for field_name, field_info in properties.items():
            desc = field_info.get("description", "")
            ftype = field_info.get("type", "string")
            field_descriptions.append(f"  - {field_name} ({ftype}): {desc}")

        json_system = (
            f"{system_prompt}\n\n"
            "IMPORTANT: You must respond with ONLY valid JSON matching this schema. "
            "Do not include any text before or after the JSON object.\n\n"
            "Required JSON fields:\n" + "\n".join(field_descriptions)
        )

        start = time.time()

        if provider == "ollama":
            text, input_tokens, output_tokens = await self._call_ollama(
                prompt, json_system, model
            )
        elif provider == "openai":
            text, input_tokens, output_tokens = await self._call_openai(
                prompt, json_system, model
            )
        elif provider == "openrouter":
            text, input_tokens, output_tokens = await self._call_openrouter(
                prompt, json_system, model
            )
        elif provider == "openai_compatible":
            text, input_tokens, output_tokens = await self._call_openai_compatible_local(
                prompt, json_system, model
            )
        else:
            raise ValueError(f"Unsupported provider for JSON-mode structured generation: {provider}")

        elapsed = time.time() - start
        cost = _calculate_cost(provider, model, input_tokens, output_tokens)

        # Parse JSON from response — strip markdown fences if present
        json_text = text.strip()
        if json_text.startswith("```"):
            # Remove markdown code fences
            lines = json_text.split("\n")
            # Drop first line (```json) and last line (```)
            lines = [l for l in lines if not l.strip().startswith("```")]
            json_text = "\n".join(lines)

        try:
            structured_data = json.loads(json_text)
        except json.JSONDecodeError:
            logger.warning("Failed to parse JSON from %s/%s response, returning as text", provider, model)
            return LLMResponse(
                text=text,
                provider=provider,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost,
                processing_time_seconds=elapsed,
                structured_data=None,
            )

        structured_data = _normalize_structured_response(structured_data)

        return LLMResponse(
            text="",
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            processing_time_seconds=elapsed,
            structured_data=structured_data,
        )
