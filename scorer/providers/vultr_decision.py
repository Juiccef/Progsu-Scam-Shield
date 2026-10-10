"""Decision models (POST /systemone): Vultr's on Vultr Serverless Inference, and TypeSafe's Jev.

A decision model writes no text. It reads the transcript, or the text message, and answers
fixed questions with probabilities: yes or no for each flag, and a graded answer for the
overall risk. Those answers are turned into the JSON reply the chat models are asked to
write, so the rest of the scorer cannot tell the difference. Nothing is generated, so there
is no reply to cut short or to validate, and the caller's words have no output to write into.
"""

from __future__ import annotations

import json
import re
from typing import Callable, Optional

import httpx

from ..flags import FLAG_IDS, label_for
from . import VULTR_INFERENCE, ProviderError
from .openai_compat import _error_message

# The risk at the middle of each of the scorer prompt's calibration bands, lowest first.
# Keep them in step with the "Calibration" line in prompts/scam-scorer-prompt.md.
RISK_ANCHORS = (5, 38, 62, 88, 100)
# What each band looks like. The model judges each level on its own, without its number or
# its neighbours, so a level describes a situation, never a degree (TypeSafe's advice).
CALL_LEVELS = (
    "An ordinary call. The caller asks for no money, codes, passwords or account details, and uses no pressure, "
    "threats or demands for secrecy.",
    "Something is unusual but nothing is demanded yet: an unexpected caller says they are from a bank, an agency or "
    "a company, or brings up an account, a payment or a problem, without pressure and without asking for money or "
    "secrets.",
    "The caller pushes: urgency, a threat of arrest, a fine or a shutoff, insisting the senior stay on the line or "
    "tell no one, or one strong request such as a code, a password or an unusual payment, while the call could "
    "still be genuine.",
    "Several strong scam signals together, such as a caller posing as an authority who demands payment by gift "
    "card, wire, cryptocurrency or courier, asks for a one-time code or a password, tells the senior to move money "
    "to a safe account or to install remote-access software, or demands secrecy.",
    "The senior is already being walked through the scam: buying gift cards, reading out codes or card numbers, "
    "moving money or installing software at the caller's direction.",
)
TEXT_LEVELS = (
    "An ordinary message: an appointment, a delivery update, a fraud alert, a one-time code sent to the reader, or "
    "a note from someone they know, asking for no money, codes, passwords or personal details. A link, a company "
    "name or a reminder on its own belongs here.",
    "Something is unusual but nothing risky is asked: a stranger who is vague about who they are, a message that "
    "does not fit the sender it claims, or an unexpected mention of an account or a payment, with no pressure.",
    "The message pushes or asks for one risky thing: a deadline, a threat to suspend an account or add a fine, or "
    "a request to pay a fee, send money or sign in through a link, while it could still be genuine.",
    "Several strong scam signals together, such as an unpaid toll or a parcel fee with a payment link, an account "
    "suspension with a sign-in link, a prize that needs a fee, a request for a password or a verification code, "
    "or someone claiming to be family on a new number asking for money.",
    "Plainly a scam: it demands gift cards, cryptocurrency, a wire transfer, a password or a verification code "
    "outright, or money for a relative in trouble, and presses for it at once.",
)
# The flags as a text shows them. The prompt for texts lists the flag IDs without describing them.
TEXT_FLAGS = {
    "AUTHORITY_IMPERSONATION": "claims to be from a government agency, the police, a bank, a delivery or toll "
    "service, a utility, a tech company or a health plan.",
    "URGENCY_PRESSURE": "demands immediate action or sets a tight deadline.",
    "THREAT": "threatens arrest, a fine, legal action, or closing, suspending or locking an account.",
    "SECRECY": "asks the reader not to tell family, the bank or anyone else.",
    "STAY_ON_LINE": "tells the reader to call a number and not hang up, or to keep replying without checking with "
    "anyone.",
    "UNUSUAL_PAYMENT": "asks for payment by gift card, prepaid card, cryptocurrency, wire transfer, or a payment "
    "app to a stranger.",
    "SAFE_ACCOUNT_TRANSFER": 'asks the reader to move money to a "secure", "safe" or "holding" account.',
    "REMOTE_ACCESS": "asks the reader to install an app or software, or to give someone control of a computer or "
    "phone.",
    "CREDENTIAL_REQUEST": "asks for a password, a PIN, a full card or account number, a Social Security number, or "
    "a sign-in through a link.",
    "OTP_REQUEST": 'asks the reader to send or read out a verification code. A code sent to the reader with "do '
    'not share it" is not this.',
    "FAMILY_EMERGENCY": "claims to be a relative or friend in trouble or on a new number, and asks for money or help.",
    "PRIZE_OR_REFUND": "offers a prize or a refund, or asks for a fee to release a parcel, a toll balance or money.",
    "VERIFICATION_AVOIDANCE": "discourages checking with the organization or the person through a known number.",
}
FLAG_THRESHOLD = 0.5  # a flag is reported when the model puts "yes" at this probability or higher

_FLAG_LINE = re.compile(r"^- ([A-Z_]+): (.+)$", re.M)
_TRANSCRIPT = re.compile(r"<transcript>\s*(.*?)\s*</transcript>", re.S)


class VultrDecisionProvider:
    def __init__(
        self,
        model: str,
        api_key: str,
        *,
        base_url: str = VULTR_INFERENCE,
        name: str = "vultr-decision",
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self.name = name
        self.model = model
        self._client = httpx.AsyncClient(
            base_url=base_url, headers={"Authorization": f"Bearer {api_key}"}, transport=transport
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
    """One yes/no question per flag, in the scorer prompt's own words, and one graded question for the risk.

    The prompt for calls names the CALLER; the one for texts does not, and is asked about the message.
    """
    call = "CALLER" in system
    described = dict(_FLAG_LINE.findall(system)) or TEXT_FLAGS
    ask = "Is this clearly present in what the CALLER says? " if call else "Is this clearly present in the message? "
    asked: dict = {
        flag: {"type": "noul", "instructions": ask + described.get(flag, flag.replace("_", " ").lower())}
        for flag in FLAG_IDS
    }
    levels = CALL_LEVELS if call else TEXT_LEVELS
    asked["risk"] = {"type": "score", "instructions": _guide(system), "criteria": list(levels)}
    return asked


def _guide(system: str) -> str:
    """The scorer prompt without its output format and flag list: who is who, the task, how to judge."""
    head, cut, rest = system.partition("Output ONLY")
    _, marker, guide = rest.partition("Scoring guide:")
    if not cut or not marker:
        # A prompt laid out the other way (the one for texts): leave out its paragraphs on the reply.
        kept = [p.strip() for p in re.split(r"\n\s*\n", system)]
        return "\n\n".join(p for p in kept if "JSON" not in p and not p.startswith("Allowed flags"))
    # previous_risk is not given to this model, so the line about it goes.
    guide = "\n".join(line for line in guide.splitlines() if "previous_risk" not in line)
    return f"{head.strip()}\n\n{marker}{guide}"


def reply(answers: dict) -> dict:
    """The fields of a scorer reply, from the model's answers."""
    anchors = RISK_ANCHORS
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
