"""One-shot text-message scoring. No call timers, transcript windows or call actions."""

from __future__ import annotations

import re
from typing import Optional

from .decision import combine_risk
from .flags import label_for
from .llm import LLMScorer
from .rules import normalize, score_text

# Inspect links before speech-oriented normalization removes their punctuation.
LINK = re.compile(r"(?:https?://|www\.)\S+|\b(?:[a-z0-9-]+\.)+[a-z]{2,24}(?:/\S*)?", re.I)
DELIVERY = re.compile(r"\b(?:parcel|package|delivery|redelivery|usps|fedex|ups|dhl|toll|ez ?pass)\b")
FEE = re.compile(r"\b(?:pay|payment|fee|unpaid|balance|owing|owed)\b")
VERIFY = re.compile(r"\b(?:verify|confirm|update|unlock|restore|validate)\b")
ACCOUNT = re.compile(r"\b(?:account|banking|password|credentials|card details|personal information)\b")
PRESSURE = re.compile(r"\b(?:urgent|immediately|suspended|locked|blocked|final notice|act now|within)\b")
# Common OTP privacy advice is not a demand to hide a transaction from family.
# Remove only this narrow clause from rule matching; other scam instructions
# still match, and the LLM always receives the complete original message.
CODE_PRIVACY_WARNING = re.compile(
    r"\b(?:never|do not|dont)\s+share\s+"
    r"(?:(?:this|that|your|the|verification|security|one time)\s+){0,3}"
    r"(?:code|otp|passcode)\s+with\s+(?:anyone|anybody|any one)\b"
)


def message_reason(flags: list[str], risk: int) -> str:
    """Fixed advice: model output and message content never become outbound SMS."""
    if "CREDENTIAL_REQUEST" in flags or "OTP_REQUEST" in flags:
        return "Verify independently before sharing account details or verification codes."
    if "PRIZE_OR_REFUND" in flags:
        return "Verify any unexpected fee, prize or refund through the organization’s official app or website."
    if risk >= 30:
        return "Check with the sender through a trusted number before paying or following instructions."
    return "No strong scam signals detected. This does not verify the sender or any links."


class MessageScorer:
    def __init__(self, llm: Optional[LLMScorer] = None) -> None:
        self.llm = llm

    async def evaluate(self, text: str) -> dict:
        normalized = normalize(text)
        rule = score_text(CODE_PRIVACY_WARNING.sub("", normalized))
        weights = dict(rule.weights)
        if LINK.search(text):
            if DELIVERY.search(normalized) and FEE.search(normalized):
                weights["PRIZE_OR_REFUND"] = max(weights.get("PRIZE_OR_REFUND", 0), 0.75)
            if VERIFY.search(normalized) and ACCOUNT.search(normalized) and PRESSURE.search(normalized):
                weights["CREDENTIAL_REQUEST"] = max(weights.get("CREDENTIAL_REQUEST", 0), 0.65)
        remaining = 1.0
        for weight in weights.values():
            remaining *= 1.0 - weight
        rule_risk = round(100 * (1 - remaining))
        outcome = await self.llm.score(text, 0) if self.llm else None
        model = outcome.result if outcome else None
        risk = combine_risk(rule_risk, model.risk if model else None)
        flags = list(dict.fromkeys([*sorted(weights, key=lambda flag: -weights[flag]), *(model.flags if model else [])]))
        return {
            "risk": risk,
            "rule_risk": rule_risk,
            "llm_risk": model.risk if model else None,
            "label": label_for(risk),
            "flags": flags,
            "explanation": message_reason(flags, risk),
            "llm_error": outcome.error if outcome else None,
        }
