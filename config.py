"""Runtime settings, read from the environment. A .env file in the repo root is loaded if present."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Mapping

from dotenv import load_dotenv

from scorer.decision import DecisionConfig

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")


def _flag(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _host(value: str) -> str:
    """Accept "abc.ngrok-free.app" or "https://abc.ngrok-free.app/" and keep the bare host."""
    return value.strip().removeprefix("https://").removeprefix("http://").removeprefix("wss://").strip("/")


def default_scorer_model(env: Mapping[str, str]) -> str:
    """The brief's default is gpt-4o-mini. Without an OpenAI key, use what we do have a key for."""
    if env.get("OPENAI_API_KEY"):
        return "gpt-4o-mini"
    if env.get("ANTHROPIC_API_KEY"):
        return "claude-haiku-4-5"
    return "rules"


@dataclass(frozen=True)
class Settings:
    # Twilio
    twilio_account_sid: str
    twilio_auth_token: str
    twilio_proxy_number: str
    twilio_sms_from: str
    senior_phone: str
    caregiver_phone: str
    senior_name: str
    public_host: str
    validate_signature: bool
    say_voice: str  # a Twilio <Say> voice; empty means Twilio's default voice (SAY_VOICE=default)
    dial_timeout_s: int
    dial_caller_id: str  # caller ID shown to the senior; empty passes the real caller's number through
    caller_track: str  # which Media Streams track carries the caller: "inbound" or "outbound"
    # Speech to text
    stt_provider: str
    deepgram_api_key: str
    deepgram_model: str
    # Scorer
    scorer_model: str  # model spec, or "rules" for rules only
    scorer_backup_model: str  # asked when scorer_model fails or runs out of time; "" for none
    scorer_prompt: Path
    llm_timeout_s: float
    llm_early_exit: bool
    window_s: float
    score_interval_s: float
    score_min_gap_s: float
    # Decisions
    decision: DecisionConfig
    allowlist: tuple[str, ...]
    # Operations
    dry_run_actions: bool
    demo_mode: bool  # allow POST /demo/call: scripted calls with no phone and no Twilio
    log_transcripts: bool
    log_dir: Path
    events_token: str
    # Sign-in (Auth0). With no domain, sign-in is off and EVENTS_TOKEN alone guards the feed.
    auth0_domain: str
    auth0_audience: str  # the API's Identifier in Auth0
    auth0_web_client_id: str  # the dashboard's application (Single Page)
    auth0_ios_client_id: str  # the iPhone app's application (Native)
    caregiver_emails: tuple[str, ...]  # who may watch calls once signed in
    # Twilio text gateway + Photon caregiver agent are opt-in.
    text_gateway_enabled: bool = False
    text_forwarding_enabled: bool = False
    photon_bridge_token: str = ""
    photon_line: str = ""  # optional dedicated caregiver line; empty uses the shared pool
    photon_caregiver_address: str = ""
    gateway_encryption_key: str = ""
    # How an approved text reaches the senior: "sms" (Twilio, from the proxy number) or "imessage"
    # (through the same Photon agent that alerts the caregiver, so no carrier registration is needed).
    text_forward_via: str = "sms"
    senior_imessage_address: str = ""  # an iMessage phone or email for the senior; empty uses SENIOR_PHONE

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "Settings":
        def get(name: str, default: str = "") -> str:
            return (env.get(name) or default).strip()

        prompt = Path(get("SCORER_PROMPT", "prompts/scam-scorer-prompt.md"))
        log_dir = Path(get("LOG_DIR", "logs"))
        return cls(
            twilio_account_sid=get("TWILIO_ACCOUNT_SID"),
            twilio_auth_token=get("TWILIO_AUTH_TOKEN"),
            twilio_proxy_number=get("TWILIO_PROXY_NUMBER"),
            twilio_sms_from=get("TWILIO_SMS_FROM") or get("TWILIO_PROXY_NUMBER"),
            senior_phone=get("SENIOR_PHONE"),
            caregiver_phone=get("CAREGIVER_PHONE"),
            senior_name=get("SENIOR_NAME", "your family member"),
            public_host=_host(get("PUBLIC_HOST")),
            validate_signature=_flag(env.get("TWILIO_VALIDATE_SIGNATURE"), True),
            say_voice="" if get("SAY_VOICE").lower() == "default" else get("SAY_VOICE", "Polly.Joanna-Neural"),
            dial_timeout_s=int(get("DIAL_TIMEOUT_S", "25")),
            dial_caller_id=get("DIAL_CALLER_ID"),
            caller_track=get("CALLER_TRACK", "inbound"),
            stt_provider=get("STT_PROVIDER", "deepgram").lower(),
            deepgram_api_key=get("DEEPGRAM_API_KEY"),
            deepgram_model=get("DEEPGRAM_MODEL", "nova-3"),
            scorer_model=get("SCORER_MODEL") or default_scorer_model(env),
            scorer_backup_model=get("SCORER_BACKUP_MODEL"),
            scorer_prompt=prompt if prompt.is_absolute() else ROOT / prompt,
            llm_timeout_s=float(get("LLM_TIMEOUT_S", "2.5")),
            llm_early_exit=_flag(env.get("LLM_EARLY_EXIT"), True),
            window_s=float(get("WINDOW_S", "40")),
            score_interval_s=float(get("SCORE_INTERVAL_S", "2.5")),
            score_min_gap_s=float(get("SCORE_MIN_GAP_S", "0.25")),
            decision=DecisionConfig(
                warn_threshold=int(get("WARN_THRESHOLD", "60")),
                end_threshold=int(get("END_THRESHOLD", "85")),
                consecutive_required=int(get("CONSECUTIVE_REQUIRED", "2")),
                grace_period_s=float(get("GRACE_PERIOD_S", "10")),
                agreement_confirms=_flag(env.get("AGREEMENT_CONFIRMS"), True),
            ),
            allowlist=tuple(entry.strip() for entry in get("ALLOWLIST").split(",") if entry.strip()),
            dry_run_actions=_flag(env.get("DRY_RUN_ACTIONS"), False),
            demo_mode=_flag(env.get("DEMO_MODE"), False),
            log_transcripts=_flag(env.get("LOG_TRANSCRIPTS"), False),
            log_dir=log_dir if log_dir.is_absolute() else ROOT / log_dir,
            events_token=get("EVENTS_TOKEN"),
            auth0_domain=_host(get("AUTH0_DOMAIN")),
            auth0_audience=get("AUTH0_AUDIENCE"),
            auth0_web_client_id=get("AUTH0_WEB_CLIENT_ID"),
            auth0_ios_client_id=get("AUTH0_IOS_CLIENT_ID"),
            caregiver_emails=tuple(e.strip().lower() for e in get("CAREGIVER_EMAILS").split(",") if e.strip()),
            text_gateway_enabled=_flag(env.get("TEXT_GATEWAY_ENABLED"), False),
            text_forwarding_enabled=_flag(env.get("TEXT_FORWARDING_ENABLED"), False),
            photon_bridge_token=get("PHOTON_BRIDGE_TOKEN"),
            photon_line=get("PHOTON_LINE"),
            photon_caregiver_address=get("PHOTON_CAREGIVER_ADDRESS") or get("CAREGIVER_PHONE"),
            gateway_encryption_key=get("GATEWAY_ENCRYPTION_KEY"),
            text_forward_via=get("TEXT_FORWARD_VIA", "sms").lower(),
            senior_imessage_address=get("SENIOR_IMESSAGE_ADDRESS"),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
