"""LLM API client with Anthropic primary and OpenAI fallback."""

from __future__ import annotations

import asyncio
import os
import time
from typing import Optional

from meeting_minutes.config import LLMConfig
from meeting_minutes.models import LLMResponse


# Cost per 1K tokens (approximate, as of early 2025)
COST_PER_1K_TOKENS: dict[str, dict[str, float]] = {
    "anthropic": {
        "claude-sonnet-4-6-20250514": 0.003,
        "claude-3-5-sonnet-20241022": 0.003,
        "claude-3-haiku-20240307": 0.00025,
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
}


def _calculate_cost(provider: str, model: str, input_tokens: int, output_tokens: int) -> float:
    provider_costs = COST_PER_1K_TOKENS.get(provider, {})
    rate = provider_costs.get(model, provider_costs.get("default", 0.003))
    return (input_tokens + output_tokens) / 1000 * rate


class LLMClient:
    """Send prompts to LLM and return raw responses. Anthropic primary, OpenAI fallback."""

    def __init__(self, config: LLMConfig) -> None:
        self._config = config

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

        response = await client.messages.create(
            model=model,
            max_tokens=self._config.max_output_tokens,
            temperature=self._config.temperature,
            system=system_prompt if system_prompt else "You are a helpful assistant.",
            messages=messages,
        )

        text = response.content[0].text if response.content else ""
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens

        return text, input_tokens, output_tokens

    async def generate_structured(
        self, prompt: str, system_prompt: str = "", tool_definition: dict | None = None
    ) -> LLMResponse:
        """Generate structured output using Anthropic tool_use. Returns LLMResponse with structured_data."""
        if tool_definition is None:
            from meeting_minutes.system2.schema import get_tool_definition
            tool_definition = get_tool_definition()

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
        response = await client.messages.create(
            model=model,
            max_tokens=self._config.max_output_tokens,
            temperature=self._config.temperature,
            system=system_prompt if system_prompt else "You are a helpful assistant.",
            messages=[{"role": "user", "content": prompt}],
            tools=[tool_definition],
            tool_choice={"type": "tool", "name": tool_definition["name"]},
        )
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
