"""Chat Completions provider for OpenAI and OpenAI-compatible servers (Gemini, Groq, Ollama, ...)."""

from __future__ import annotations

import json
from typing import Callable, Optional

import httpx

from . import ProviderError, strict_json_setting


class OpenAICompatProvider:
    def __init__(
        self, name: str, model: str, base_url: str, api_key: str, *, transport: Optional[httpx.AsyncBaseTransport] = None
    ) -> None:
        self.name = name
        self.model = model
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), headers=headers, transport=transport)
        # Request shape, relaxed on the fly when a server rejects a parameter.
        strict = strict_json_setting()
        if strict is None:
            strict = name == "openai"
        self._response_format = "json_schema" if strict else "json_object"
        self._token_param = "max_tokens"
        self._send_temperature = True

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
                async with self._client.stream(
                    "POST", "/chat/completions", json=self._payload(system, messages, schema), timeout=timeout_s
                ) as response:
                    if response.status_code >= 400:
                        await response.aread()
                        error = _error_message(response)
                        if response.status_code == 400 and self._relax(error):
                            continue
                        raise ProviderError(f"HTTP {response.status_code}: {error[:200]}")
                    return await _read_stream(response, stop_when)
            except httpx.TimeoutException as exc:
                raise TimeoutError from exc
            except httpx.HTTPError as exc:
                raise ProviderError(f"connection error: {type(exc).__name__}") from exc
        raise ProviderError("the server rejected every request shape")

    def _payload(self, system: str, messages: list[dict], schema: dict) -> dict:
        payload: dict = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *messages],
            "stream": True,
            # Reasoning models spend completion tokens on reasoning, so they get more room.
            self._token_param: 400 if self._token_param == "max_tokens" else 2000,
        }
        if self._send_temperature:
            payload["temperature"] = 0
        if self._response_format == "json_schema":
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "scam_score", "strict": True, "schema": schema},
            }
        elif self._response_format == "json_object":
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _relax(self, error: str) -> bool:
        """Drop the parameter the server complained about. Returns False if nothing is left to drop."""
        error = error.lower()
        if "max_tokens" in error and self._token_param == "max_tokens":
            self._token_param = "max_completion_tokens"
            return True
        if "temperature" in error and self._send_temperature:
            self._send_temperature = False
            return True
        if ("response_format" in error or "schema" in error) and self._response_format != "none":
            self._response_format = "json_object" if self._response_format == "json_schema" else "none"
            return True
        return False

    async def aclose(self) -> None:
        await self._client.aclose()


async def _read_stream(response: httpx.Response, stop_when: Optional[Callable[[str], bool]]) -> str:
    """Accumulate the streamed reply, stopping early once `stop_when` is satisfied."""
    text = ""
    async for line in response.aiter_lines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            choices = json.loads(data).get("choices") or []
        except ValueError:
            continue
        if not choices:
            continue
        delta = choices[0].get("delta") or {}
        if delta.get("refusal"):
            raise ProviderError("model refused")
        text += delta.get("content") or ""
        if stop_when is not None and stop_when(text):
            break
    return text


def _error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text
    if isinstance(body, list) and body:  # Gemini's compatibility layer wraps errors in a list
        body = body[0]
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        return str(error.get("message") or error)
    return str(error or response.text)
