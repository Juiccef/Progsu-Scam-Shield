"""Model providers for the LLM scorer.

A model spec is a bare model ID ("gpt-4o-mini", "claude-haiku-4-5", "gemini-...")
or "provider:model" ("groq:llama-3.3-70b-versatile", "custom:llama3.2:3b").
"""

from __future__ import annotations

import os
from typing import Callable, Optional, Protocol


class ProviderError(Exception):
    """The provider could not return a usable completion."""


class Provider(Protocol):
    name: str
    model: str

    async def complete(
        self,
        system: str,
        messages: list[dict],
        schema: dict,
        timeout_s: float,
        stop_when: Optional[Callable[[str], bool]] = None,
    ) -> str:
        """Return the model's reply text, cut short once `stop_when(text so far)` is true.

        Raise TimeoutError or ProviderError on failure.
        """

    async def aclose(self) -> None: ...


def strict_json_setting() -> Optional[bool]:
    """LLM_STRICT_JSON: true/false forces schema-enforced output on or off; unset keeps each provider's default."""
    value = os.environ.get("LLM_STRICT_JSON", "").strip().lower()
    if not value:
        return None
    return value in ("1", "true", "yes", "on")


# name -> (base URL, API key env vars in order of preference)
OPENAI_COMPATIBLE = {
    "openai": ("https://api.openai.com/v1", ("OPENAI_API_KEY",)),
    "google": ("https://generativelanguage.googleapis.com/v1beta/openai", ("GEMINI_API_KEY", "GOOGLE_API_KEY")),
    "groq": ("https://api.groq.com/openai/v1", ("GROQ_API_KEY",)),
    "custom": ("", ("LLM_API_KEY",)),  # any OpenAI-compatible server; set LLM_BASE_URL
}
PROVIDER_NAMES = ("anthropic", *OPENAI_COMPATIBLE)


def resolve(spec: str) -> tuple[str, str]:
    """Split a model spec into (provider name, model ID)."""
    prefix, _, rest = spec.partition(":")
    if rest and prefix.lower() in PROVIDER_NAMES:
        return prefix.lower(), rest
    lowered = spec.lower()
    if lowered.startswith("claude"):
        return "anthropic", spec
    if lowered.startswith(("gpt-", "o1", "o3", "o4", "chatgpt")):
        return "openai", spec
    if lowered.startswith("gemini"):
        return "google", spec
    raise ValueError(
        f"Cannot tell which provider serves {spec!r}. Use provider:model with one of: {', '.join(PROVIDER_NAMES)}."
    )


def make_provider(spec: str) -> Provider:
    name, model = resolve(spec)
    if name == "anthropic":
        from .anthropic_provider import AnthropicProvider

        return AnthropicProvider(model)

    from .openai_compat import OpenAICompatProvider

    base_url, key_vars = OPENAI_COMPATIBLE[name]
    if name == "custom":
        base_url = os.environ.get("LLM_BASE_URL", "")
        if not base_url:
            raise ValueError("custom: models need LLM_BASE_URL (an OpenAI-compatible endpoint).")
    api_key = next((os.environ[var] for var in key_vars if os.environ.get(var)), "")
    if not api_key and name != "custom":
        raise ValueError(f"{spec} needs {' or '.join(key_vars)} to be set.")
    return OpenAICompatProvider(name, model, base_url, api_key)
