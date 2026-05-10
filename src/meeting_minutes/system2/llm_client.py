"""LLM API client with multi-provider support (Anthropic, OpenAI, OpenRouter, Ollama, OpenAI-compatible local)."""

from __future__ import annotations

import asyncio
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

    async def generate(self, prompt: str, system_prompt: str = "") -> LLMResponse:
        """Send prompt to configured LLM provider. Returns response with token usage."""
        # Try primary provider
        for attempt in range(self._config.retry_attempts):
            try:
                return await self._generate_with_provider(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    provider=self._config.primary_provider,
                    model=self._config.model,
                )
            except Exception as exc:
                if attempt < self._config.retry_attempts - 1:
                    wait = 2 ** attempt
                    await asyncio.sleep(wait)
                else:
                    # Try fallback
                    if self._config.fallback_provider and self._config.fallback_model:
                        try:
                            return await self._generate_with_provider(
                                prompt=prompt,
                                system_prompt=system_prompt,
                                provider=self._config.fallback_provider,
                                model=self._config.fallback_model,
                            )
                        except Exception as fallback_exc:
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

        response = await client.messages.create(**kwargs)

        text = response.content[0].text if response.content else ""
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens

        return text, input_tokens, output_tokens

    async def generate_structured(
        self, prompt: str, system_prompt: str = "", tool_definition: dict | None = None
    ) -> LLMResponse:
        """Generate structured output. Uses Anthropic tool_use when available, JSON mode for others."""
        if tool_definition is None:
            from meeting_minutes.system2.schema import get_tool_definition
            tool_definition = get_tool_definition()

        provider = self._config.primary_provider

        # Non-Anthropic providers: use JSON-mode structured generation
        if provider in ("ollama", "openai", "openrouter", "openai_compatible"):
            for attempt in range(self._config.retry_attempts):
                try:
                    return await self._generate_structured_via_json(
                        prompt=prompt,
                        system_prompt=system_prompt,
                        provider=provider,
                        model=self._config.model,
                        tool_definition=tool_definition,
                    )
                except Exception as exc:
                    if attempt < self._config.retry_attempts - 1:
                        wait = 2 ** attempt
                        await asyncio.sleep(wait)
                    else:
                        raise RuntimeError(
                            f"Structured generation failed after {self._config.retry_attempts} attempts: {exc}"
                        ) from exc

        # Anthropic: native tool_use
        for attempt in range(self._config.retry_attempts):
            try:
                return await self._call_anthropic_structured(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    model=self._config.model,
                    tool_definition=tool_definition,
                )
            except Exception as exc:
                if attempt < self._config.retry_attempts - 1:
                    wait = 2 ** attempt
                    await asyncio.sleep(wait)
                else:
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

        response = await client.messages.create(**kwargs)
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
