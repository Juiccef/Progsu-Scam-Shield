"""Only the Python worker may send approved relay jobs through Twilio.

Retries apply to receiving/scoring, never to an uncertain provider send. The
SQLite attempt is claimed before calling Twilio and recovered as unknown after a crash.
"""
from __future__ import annotations

import asyncio
import logging
import re

from twilio.base.exceptions import TwilioRestException
from twilio.http.http_client import TwilioHttpClient
from twilio.rest import Client

from gateway import MAX_SMS_LENGTH, SendResult

log = logging.getLogger("shield.gateway.sms")


class SMSDelivery:
    def __init__(self, store, *, client_factory=None, public_host=lambda: "", timeout_s=15):
        self.store = store
        self.settings = store.settings
        self.public_host = public_host
        self.timeout_s = timeout_s
        quiet = logging.Logger("shield.gateway.twilio_http")
        quiet.disabled = True
        self.client_factory = client_factory or (lambda: Client(
            self.settings.twilio_account_sid, self.settings.twilio_auth_token,
            http_client=TwilioHttpClient(timeout=10, max_retries=0, logger=quiet),
        ))
        self.client = None

    @property
    def ready(self):
        s = self.settings
        return s.text_forwarding_enabled and not s.dry_run_actions and bool(s.twilio_account_sid and s.twilio_auth_token)

    async def drain(self):
        if not self.ready:
            return False
        job = self.store.claim("relay")
        if not job:
            return False
        outcome, sid = "delivery_unknown", None
        try:
            # Destinations and the SMS sender always come from server configuration.
            if (job["recipient"] != self.settings.senior_phone or job["line"] != self.settings.twilio_proxy_number
                    or len(job["text"]) > MAX_SMS_LENGTH):
                raise ValueError("Unexpected approved SMS job")
            params = {"to": self.settings.senior_phone, "from_": self.settings.twilio_proxy_number, "body": job["text"]}
            host = self.public_host()
            if host:
                params["status_callback"] = f"https://{host}/gateway/sms/status/{job['job_id']}/{job['attempt_id']}"

            def send():
                if self.client is None:
                    self.client = self.client_factory()
                return self.client.messages.create(**params)

            message = await asyncio.wait_for(asyncio.to_thread(send), timeout=self.timeout_s)
            if not re.fullmatch(r"(?:SM|MM)[0-9a-fA-F]{32}", getattr(message, "sid", "") or ""):
                raise ValueError("Missing Twilio acknowledgement")
            sid = message.sid
            outcome = "delivery_failed" if getattr(message, "status", None) in {"failed", "undelivered", "canceled"} else "sent"
            # Accepted by Twilio; carrier delivery is a separate callback.
        except TwilioRestException as exc:
            # A definitive client rejection failed; timeouts/5xx/429 remain uncertain.
            if exc.status is not None and 400 <= exc.status < 500 and exc.status not in {408, 429}:
                outcome = "delivery_failed"
            log.warning("Twilio gateway send did not complete; automatic resend suppressed.")
        except asyncio.CancelledError:
            self.store.complete(job["job_id"], SendResult(attempt_id=job["attempt_id"], outcome="delivery_unknown"), kind="relay")
            raise
        except Exception:
            # Never log SDK exceptions or job content: both can contain private text.
            log.warning("Twilio gateway send was not confirmed; automatic resend suppressed.")
        self.store.complete(job["job_id"], SendResult(attempt_id=job["attempt_id"], outcome=outcome), kind="relay", provider_sid=sid)
        return True

    async def run(self):
        while True:
            try:
                await self.drain()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("SMS queue operation unavailable; claimed sends will not be repeated.")
            await asyncio.sleep(1)
