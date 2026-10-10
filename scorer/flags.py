"""Flag IDs and risk labels shared by the rule scorer, the LLM scorer and the actions.

The IDs must match the list in prompts/scam-scorer-prompt.md.
"""

FLAG_IDS = (
    "AUTHORITY_IMPERSONATION",
    "URGENCY_PRESSURE",
    "THREAT",
    "SECRECY",
    "STAY_ON_LINE",
    "UNUSUAL_PAYMENT",
    "SAFE_ACCOUNT_TRANSFER",
    "REMOTE_ACCESS",
    "CREDENTIAL_REQUEST",
    "OTP_REQUEST",
    "FAMILY_EMERGENCY",
    "PRIZE_OR_REFUND",
    "VERIFICATION_AVOIDANCE",
)

CALLER = "CALLER"
SENIOR = "SENIOR"


def label_for(risk: int) -> str:
    """Label mapping from the scorer prompt: safe 0-29, suspicious 30-69, likely_scam 70-100."""
    if risk >= 70:
        return "likely_scam"
    if risk >= 30:
        return "suspicious"
    return "safe"
