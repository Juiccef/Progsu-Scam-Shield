"""Claude provider, using the official Anthropic SDK."""

from __future__ import annotations

from typing import Callable, Optional

import anthropic

from . import ProviderError, strict_json_setting

# Models that still accept sampling parameters. Newer ones reject `temperature`
# and always think, so they get `effort: low` instead to keep latency down.
_TEMPERATURE_MODELS = ("haiku-4-5", "sonnet-4-6", "opus-4-6", "sonnet-4-5", "opus-4-5", "opus-4-1", "claude-3")


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str) -> None:
        self.model = model
        # Credentials come from the environment (ANTHROPIC_API_KEY or an `ant auth login` profile).
        # No SDK retries: a retry would blow the scorer's latency budget, and the rules cover a miss.
        self._client = anthropic.AsyncAnthropic(max_retries=0)
        self._send_temperature = any(tag in model for tag in _TEMPERATURE_MODELS)
        self._send_effort = not self._send_temperature
        # Schema-enforced output measured ~0.7 s slower per call on Haiku 4.5, so it is opt-in
        # (LLM_STRICT_JSON=true). The prompt already demands JSON only and the reply is validated.
        self._structured = strict_json_setting() is True

    async def complete(
        self,
        system: str,
        messages: list[dict],
        schema: dict,
        timeout_s: float,
        stop_when: Optional[Callable[[str], bool]] = None,
    ) -> str:
        for _ in range(4):
            try:
                return await self._stream(system, messages, schema, timeout_s, stop_when)
            except anthropic.APITimeoutError as exc:
                raise TimeoutError from exc
            except anthropic.BadRequestError as exc:
                if self._relax(exc.message):
                    continue
                raise ProviderError(f"bad request: {exc.message[:200]}") from exc
            except anthropic.AuthenticationError as exc:
                raise ProviderError("authentication failed (check ANTHROPIC_API_KEY)") from exc
            except anthropic.NotFoundError as exc:
                raise ProviderError(f"unknown model {self.model!r}") from exc
            except anthropic.RateLimitError as exc:
                raise ProviderError("rate limited") from exc
            except anthropic.APIStatusError as exc:
                raise ProviderError(f"HTTP {exc.status_code}: {exc.message[:200]}") from exc
            except anthropic.APIConnectionError as exc:
                raise ProviderError("connection error") from exc
        raise ProviderError("the API rejected every request shape")

    async def _stream(
        self, system: str, messages: list[dict], schema: dict, timeout_s: float, stop_when: Optional[Callable[[str], bool]]
    ) -> str:
        text = ""
        async with self._client.messages.stream(**self._request(system, messages, schema), timeout=timeout_s) as stream:
            async for chunk in stream.text_stream:
                text += chunk
                if stop_when is not None and stop_when(text):
                    return text  # leaving the block closes the stream
            message = await stream.get_final_message()
        if message.stop_reason == "refusal":
            raise ProviderError("model refused")
        return text

    def _request(self, system: str, messages: list[dict], schema: dict) -> dict:
        request: dict = {
            "model": self.model,
            # Thinking models spend output tokens on thinking, so they get more room.
            "max_tokens": 512 if self._send_temperature else 4096,
            "system": system,
            "messages": messages,
        }
        if self._send_temperature:
            # The 1.x SDK dropped `temperature` from its signature; the API still honors it on these models.
            request["extra_body"] = {"temperature": 0}
        output_config: dict = {}
        if self._structured:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        if self._send_effort:
            output_config["effort"] = "low"
        if output_config:
            request["output_config"] = output_config
        return request

    def _relax(self, error: str) -> bool:
        """Drop the parameter the API complained about. Returns False if nothing is left to drop."""
        error = error.lower()
        if "temperature" in error and self._send_temperature:
            self._send_temperature = False
            return True
        if "effort" in error and self._send_effort:
            self._send_effort = False
            return True
        if ("output_config" in error or "schema" in error or "format" in error) and self._structured:
            self._structured = False
            return True
        return False

    async def aclose(self) -> None:
        await self._client.close()
