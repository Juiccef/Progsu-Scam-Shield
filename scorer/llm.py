"""LLM scam scorer: loads the prompt file, calls the model, returns a validated result.

The model is pluggable (see scorer/providers). On a timeout or an unusable reply
the outcome carries no result and the caller falls back to the rules alone.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .flags import FLAG_IDS, label_for
from .providers import Provider, ProviderError, make_provider

log = logging.getLogger("shield.llm")

RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "risk": {"type": "integer"},
        "label": {"type": "string", "enum": ["safe", "suspicious", "likely_scam"]},
        "flags": {"type": "array", "items": {"type": "string", "enum": list(FLAG_IDS)}},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "explanation": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": ["risk", "label", "flags", "evidence", "explanation", "confidence"],
    "additionalProperties": False,
}

DEFAULT_USER_TEMPLATE = (
    "previous_risk: {previous_risk}\nTranscript (most recent last):\n<transcript>\n{transcript}\n</transcript>"
)

# The reply up to the end of its flags list: everything a decision needs.
_DECISION_FIELDS = re.compile(r'\{.*?"risk"\s*:\s*-?\d+(?:\.\d+)?.*?"flags"\s*:\s*\[[^\]]*\]', re.S)
_EXAMPLE = re.compile(r"Input transcript:\s*\n(?P<transcript>.*?)\n\s*\nOutput:\s*\n(?P<output>\{[^\n]*\})", re.S)
_TRANSCRIPT_BLOCK = re.compile(r"<transcript>.*?</transcript>", re.S)


@dataclass(frozen=True)
class LLMResult:
    risk: int
    label: str
    flags: tuple[str, ...]
    evidence: tuple[str, ...]
    explanation: str
    confidence: float


@dataclass(frozen=True)
class LLMOutcome:
    result: Optional[LLMResult]
    latency_s: float
    error: Optional[str]  # "timeout", "bad_output: ...", "provider: ..." when result is None


class ScorerPrompt:
    """The system prompt, few-shot examples and user template parsed from the prompt markdown file."""

    def __init__(self, system: str, examples: list[tuple[str, str]], user_template: str = DEFAULT_USER_TEMPLATE) -> None:
        self.system = system
        self.examples = examples
        self.user_template = user_template

    @classmethod
    def load(cls, path: str | Path) -> "ScorerPrompt":
        text = Path(path).read_text(encoding="utf-8")
        system = _section(text, "## BEGIN SYSTEM PROMPT", "## END SYSTEM PROMPT") or text.strip()
        after_system = text.split("## END SYSTEM PROMPT", 1)[-1]
        examples = []
        for match in _EXAMPLE.finditer(after_system):
            try:
                json.loads(match["output"])
            except json.JSONDecodeError:
                log.warning("Skipping a few-shot example in %s: its output is not valid JSON", path)
                continue
            examples.append((match["transcript"].strip(), match["output"].strip()))
        return cls(system, examples, _user_template(text))

    def user_message(self, transcript: str, previous_risk: int) -> str:
        return self.user_template.replace("{previous_risk}", str(previous_risk)).replace("{transcript}", transcript)

    def messages(self, transcript: str, previous_risk: int) -> list[dict]:
        """Few-shot examples as prior turns, then the transcript to score."""
        messages: list[dict] = []
        for example_transcript, example_output in self.examples:
            messages.append({"role": "user", "content": self.user_message(example_transcript, 0)})
            messages.append({"role": "assistant", "content": example_output})
        messages.append({"role": "user", "content": self.user_message(transcript, previous_risk)})
        return messages


def _section(text: str, start: str, end: str) -> str:
    if start not in text or end not in text:
        return ""
    return text.split(start, 1)[1].split(end, 1)[0].strip()


def _user_template(text: str) -> str:
    marker = "## User message template"
    if marker not in text:
        return DEFAULT_USER_TEMPLATE
    body = re.split(r"^## ", text.split(marker, 1)[1], maxsplit=1, flags=re.M)[0].strip()
    template = _TRANSCRIPT_BLOCK.sub("<transcript>\n{transcript}\n</transcript>", body)
    if "{previous_risk}" not in template or "{transcript}" not in template:
        return DEFAULT_USER_TEMPLATE
    return template


def parse_result(raw: str) -> LLMResult:
    """Validate the model's reply. Raises ValueError when it is unusable."""
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in the reply")
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON ({exc.msg})") from exc
    if not isinstance(data, dict) or "risk" not in data:
        raise ValueError("the reply has no risk field")
    try:
        risk = max(0, min(100, round(float(data["risk"]))))
    except (TypeError, ValueError) as exc:
        raise ValueError("risk is not a number") from exc
    try:
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0.5))))
    except (TypeError, ValueError):
        confidence = 0.5
    flags = tuple(dict.fromkeys(flag for flag in _strings(data.get("flags")) if flag in FLAG_IDS))
    evidence = tuple(snippet[:160] for snippet in _strings(data.get("evidence"))[:3])
    explanation = data.get("explanation")
    return LLMResult(
        risk=risk,
        label=label_for(risk),
        flags=flags,
        evidence=evidence,
        explanation=explanation[:240] if isinstance(explanation, str) else "",
        confidence=confidence,
    )


def _strings(value: object) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def has_decision_fields(text: str) -> bool:
    return _DECISION_FIELDS.search(text) is not None


def parse_reply(raw: str) -> LLMResult:
    """Parse a complete reply, or one cut short after its flags list."""
    try:
        return parse_result(raw)
    except ValueError:
        match = _DECISION_FIELDS.search(raw)
        if match is None:
            raise
        return parse_result(match.group(0) + "}")


class LLMScorer:
    """`early_exit` stops reading the reply once risk and flags have arrived. That saves roughly
    half a second per evaluation and drops the evidence and explanation fields, which no decision uses."""

    def __init__(
        self,
        model: str,
        prompt: ScorerPrompt,
        *,
        timeout_s: float = 2.5,
        early_exit: bool = True,
        provider: Optional[Provider] = None,
    ) -> None:
        self.model = model
        self.prompt = prompt
        self.timeout_s = timeout_s
        self.early_exit = early_exit
        self._provider = provider or make_provider(model)

    async def score(self, transcript: str, previous_risk: int = 0, *, timeout_s: Optional[float] = None) -> LLMOutcome:
        timeout_s = timeout_s or self.timeout_s
        started = time.perf_counter()
        result, error = None, None
        try:
            raw = await asyncio.wait_for(
                self._provider.complete(
                    self.prompt.system,
                    self.prompt.messages(transcript, previous_risk),
                    RESULT_SCHEMA,
                    timeout_s,
                    has_decision_fields if self.early_exit else None,
                ),
                timeout=timeout_s,
            )
            result = parse_reply(raw)
        except TimeoutError:
            error = "timeout"
        except ProviderError as exc:
            error = f"provider: {exc}"
        except ValueError as exc:
            error = f"bad_output: {exc}"
        except Exception as exc:  # never let a scorer bug take the call down
            # Provider exceptions can contain message bodies. Log the type only.
            log.warning("Unexpected LLM scorer failure (%s)", type(exc).__name__)
            error = f"error: {type(exc).__name__}"
        latency_s = time.perf_counter() - started
        if error:
            log.warning("LLM scorer fell back to rules (%s) model=%s after %.2fs", error.split(":", 1)[0], self.model, latency_s)
        return LLMOutcome(result, latency_s, error)

    async def warm_up(self) -> None:
        """Open the connection and compile the output schema before the first real evaluation."""
        await self.score("CALLER: Hello.", 0, timeout_s=max(self.timeout_s, 10.0))

    async def aclose(self) -> None:
        await self._provider.aclose()
