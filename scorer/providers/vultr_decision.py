"""Vultr decision models (POST /systemone on Vultr Serverless Inference).

A decision model writes no text. It reads the transcript and answers fixed questions with
probabilities: yes or no for each flag, and a graded answer for the overall risk. Those
answers are turned into the JSON reply the chat models are asked to write, so the rest of
the scorer cannot tell the difference. Nothing is generated, so there is no reply to cut
short or to validate, and the caller's words have no output to write into.
"""

from __future__ import annotations

import json
import re
from typing import Callable, Optional

import httpx

from ..flags import FLAG_IDS, label_for
from . import VULTR_INFERENCE, ProviderError
from .openai_compat import _error_message

# The scorer prompt's calibration bands, lowest first, each with the risk at its middle.
# Keep them in step with the "Calibration" line in prompts/scam-scorer-prompt.md.
RISK_LEVELS = (
    ("Risk 0-10: clearly ordinary.", 5),
    ("Risk 30-45: unusual but unclear.", 38),
    ("Risk 55-70: several pressure signals, or one strong signal with real doubt.", 62),
    ("Risk 80-95: multiple strong signals.", 88),
    ("Risk 100: unmistakably a scam.", 100),
)
FLAG_THRESHOLD = 0.5  # a flag is reported when the model puts "yes" at this probability or higher

_FLAG_LINE = re.compile(r"^- ([A-Z_]+): (.+)$", re.M)
_TRANSCRIPT = re.compile(r"<transcript>\s*(.*?)\s*</transcript>", re.S)


class VultrDecisionProvider:
    name = "vultr-decision"

    def __init__(self, model: str, api_key: str, *, transport: Optional[httpx.AsyncBaseTransport] = None) -> None:
        self.model = model
        self._client = httpx.AsyncClient(
            base_url=VULTR_INFERENCE, headers={"Authorization": f"Bearer {api_key}"}, transport=transport
        )

    async def complete(
        self,
        system: str,
        messages: list[dict],
        schema: dict,
        timeout_s: float,
        stop_when: Optional[Callable[[str], bool]] = None,
    ) -> str:
        # Only the transcript is sent. The few-shot turns and previous_risk are for models that write.
        content = messages[-1]["content"]
        fenced = _TRANSCRIPT.search(content)
        payload = {"model": self.model, "state": fenced[1] if fenced else content, "questions": questions(system)}
        try:
            response = await self._client.post("/systemone", json=payload, timeout=timeout_s)
        except httpx.TimeoutException as exc:
            raise TimeoutError from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"connection error: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            raise ProviderError(f"HTTP {response.status_code}: {_error_message(response)[:200]}")
        try:
            return json.dumps(reply(response.json()["answers"]))
        except (AttributeError, KeyError, IndexError, TypeError, ValueError) as exc:
            # The answers hold our own question IDs and numbers, never transcript text.
            raise ProviderError(f"unexpected answers: {response.text[:200]}") from exc

    async def aclose(self) -> None:
        await self._client.aclose()


def questions(system: str) -> dict:
    """One yes/no question per flag, in the scorer prompt's own words, and one graded question for the risk."""
    described = dict(_FLAG_LINE.findall(system))
    asked: dict = {
        flag: {
            "type": "noul",
            "instructions": "Is this clearly present in what the CALLER says? "
            + described.get(flag, flag.replace("_", " ").lower()),
        }
        for flag in FLAG_IDS
    }
    asked["risk"] = {"type": "score", "instructions": _guide(system), "criteria": [level for level, _ in RISK_LEVELS]}
    return asked


def _guide(system: str) -> str:
    """The scorer prompt without its output format and flag list: who is who, the task, how to judge."""
    head, cut, rest = system.partition("Output ONLY")
    _, marker, guide = rest.partition("Scoring guide:")
    if not cut or not marker:
        return system
    # previous_risk is not given to this model, so the line about it goes.
    guide = "\n".join(line for line in guide.splitlines() if "previous_risk" not in line)
    return f"{head.strip()}\n\n{marker}{guide}"


def reply(answers: dict) -> dict:
    """The fields of a scorer reply, from the model's answers."""
    anchors = [risk for _, risk in RISK_LEVELS]
    graded = answers["risk"]
    probabilities = graded.get("probabilities")
    if probabilities:
        # The risk the model expects, over its whole spread of answers.
        risk = sum(float(p) * anchors[int(level)] for level, p in probabilities.items())
    else:
        # Only the weighted level came back: read between the two bands it falls between.
        level = min(max(float(graded["score"]), 0.0), len(anchors) - 1.0)
        low = min(int(level), len(anchors) - 2)
        risk = anchors[low] + (level - low) * (anchors[low + 1] - anchors[low])
    yes = {flag: float((answers.get(flag) or {}).get("noul", 0.0)) for flag in FLAG_IDS}
    flags = sorted((flag for flag in FLAG_IDS if yes[flag] >= FLAG_THRESHOLD), key=lambda flag: -yes[flag])
    confidence = graded.get("confidence")
    return {
        "risk": round(risk),
        "label": label_for(round(risk)),
        "flags": flags,
        "evidence": [],
        "explanation": "",
        "confidence": 0.5 if confidence is None else float(confidence),
    }
