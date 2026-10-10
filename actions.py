"""What Shield does when the decision engine fires: warn the caregiver, or end the call.

Every text sent to the caregiver is built from fixed templates and flag IDs. Nothing the
caller said, and nothing the LLM wrote, is ever put in an SMS or spoken to the senior.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Iterable, Optional

from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse

from config import Settings
from events import EventBus, mask_phone

log = logging.getLogger("shield.actions")

END_MESSAGE = (
    "Shield has detected a likely scam and is ending this call. You are safe. Your caregiver has been notified."
)

# Most telling first: the SMS names the top two.
REASONS = {
    "UNUSUAL_PAYMENT": "asked for payment by gift card, wire, crypto or cash",
    "SAFE_ACCOUNT_TRANSFER": "asked to move money to a 'safe' account",
    "REMOTE_ACCESS": "asked for remote access to a device",
    "OTP_REQUEST": "asked for a verification code",
    "CREDENTIAL_REQUEST": "asked for an SSN, PIN, password or account number",
    "SECRECY": "demanded secrecy",
    "FAMILY_EMERGENCY": "claimed a relative urgently needs money",
    "PRIZE_OR_REFUND": "promised a prize or refund for a fee",
    "VERIFICATION_AVOIDANCE": "discouraged checking with the bank or family",
    "THREAT": "threatened arrest, shutoff or legal action",
    "STAY_ON_LINE": "pressed them to stay on the line",
    "URGENCY_PRESSURE": "applied time pressure",
    "AUTHORITY_IMPERSONATION": "claimed to be an official or company agent",
}


def reason_for(flags: Iterable[str]) -> str:
    """One sentence for the caregiver, built only from flag IDs."""
    present = set(flags)
    top = [phrase for flag, phrase in REASONS.items() if flag in present][:2]
    if not top:
        return "The caller's requests matched common scam patterns."
    return f"The caller {' and '.join(top)}."


class Actions:
    def __init__(
        self,
        settings: Settings,
        bus: EventBus,
        *,
        client_factory: Optional[Callable[[], Client]] = None,
        public_host: Callable[[], str] = lambda: "",
    ) -> None:
        self.settings = settings
        self.bus = bus
        self._client_factory = client_factory or (
            lambda: Client(settings.twilio_account_sid, settings.twilio_auth_token)
        )
        self._public_host = public_host
        self._client: Optional[Client] = None

    @property
    def live(self) -> bool:
        """False in dry-run mode or without Twilio credentials: actions are logged, not sent."""
        s = self.settings
        return not s.dry_run_actions and bool(s.twilio_account_sid and s.twilio_auth_token)

    def _twilio(self) -> Client:
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    async def warn(self, call_sid: str, reason: str) -> dict:
        """Text the caregiver. `reason` must come from reason_for()."""
        body = (
            f"Shield warning: a call to {self.settings.senior_name} shows signs of a scam. "
            f"{reason} The call is still in progress."
        )
        return {"sms": await self._sms(call_sid, body)}

    async def end(self, call_sid: str, child_sid: Optional[str], reason: str) -> dict:
        """Speak the goodbye to the senior and hang up, then text the caregiver."""
        if not self.live:
            call_control = "dry_run"
        else:
            try:
                call_control = await asyncio.to_thread(self._end_call, call_sid, child_sid)
            except Exception as exc:
                log.error("Could not end call %s: %s", call_sid, _describe(exc))
                call_control = f"failed: {_describe(exc)}"
        call_control_at = time.time()
        body = f"Shield ended a likely scam call to {self.settings.senior_name}. {reason}"
        return {
            "call_control": call_control,
            "call_control_at": call_control_at,
            "sms": await self._sms(call_sid, body),
        }

    def _end_call(self, call_sid: str, child_sid: Optional[str]) -> str:
        client = self._twilio()
        child_sid = child_sid or self._find_child(client, call_sid)
        if child_sid:
            response = VoiceResponse()
            response.say(END_MESSAGE, **self._voice())
            response.hangup()
            try:
                # Redirecting the senior's leg breaks the bridge; the caller's leg falls through
                # to the TwiML after <Dial> and is hung up there.
                client.calls(child_sid).update(twiml=str(response))
                return "child_redirect"
            except TwilioRestException as exc:
                log.warning("Could not redirect the senior's leg (%s); hanging up the caller's leg", _describe(exc))
        else:
            log.warning("No call leg found for the senior on %s; hanging up the caller's leg", call_sid)
        client.calls(call_sid).update(twiml="<Response><Hangup/></Response>")
        return "parent_hangup"

    @staticmethod
    def _find_child(client: Client, call_sid: str) -> Optional[str]:
        """Fallback when the status callback with the child leg's SID has not arrived."""
        try:
            legs = client.calls.list(parent_call_sid=call_sid, limit=5)
        except TwilioRestException as exc:
            log.warning("Could not list call legs for %s (%s)", call_sid, _describe(exc))
            return None
        live = [leg for leg in legs if leg.status in ("in-progress", "ringing")]
        return live[0].sid if live else None

    async def _sms(self, call_sid: str, body: str) -> str:
        """Text the caregiver. A blocked or failed SMS is reported, never raised."""
        s = self.settings
        if not s.caregiver_phone:
            return "skipped: CAREGIVER_PHONE is not set"
        if not self.live:
            log.info("[dry run] SMS to %s: %s", mask_phone(s.caregiver_phone), body)
            return "dry_run"
        if not s.twilio_sms_from:
            return "skipped: no sender number (TWILIO_SMS_FROM or TWILIO_PROXY_NUMBER)"
        params = {"to": s.caregiver_phone, "from_": s.twilio_sms_from, "body": body}
        host = self._public_host()
        if host:
            params["status_callback"] = f"https://{host}/sms/status"
        try:
            message = await asyncio.to_thread(lambda: self._twilio().messages.create(**params))
        except Exception as exc:
            log.warning("Caregiver SMS failed for %s: %s", call_sid, _describe(exc))
            return f"failed: {_describe(exc)}"
        return f"queued: {message.sid}"

    def _voice(self) -> dict:
        return {"voice": self.settings.say_voice} if self.settings.say_voice else {}


def _describe(exc: Exception) -> str:
    if isinstance(exc, TwilioRestException):
        return f"Twilio error {exc.code} (HTTP {exc.status})"
    return type(exc).__name__
