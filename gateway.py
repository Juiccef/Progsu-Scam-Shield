"""Persistent, encrypted Twilio screening queue and Photon caregiver interfaces.

One backend worker owns this database. Message content is never put in the event
bus or outbox table: relay jobs decrypt it only when claimed by the transport.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from typing import Literal

from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, model_validator

from events import mask_phone
from scorer.messages import message_reason

TTL = 24 * 60 * 60
SEND_LEASE = 90
MAX_SMS_LENGTH = 1600
NONRELEASABLE_REASONS = {"unsupported_content", "message_too_long", "transport_changed"}
# What the iMessage agent may take from the outbox, and no more. A text for the senior goes out as
# "relay" (Twilio SMS) or "senior_imessage" (Photon), depending on TEXT_FORWARD_VIA, never both.
PHOTON_JOB_KINDS = ("caregiver_alert", "senior_imessage")
RELAY_KINDS = ("relay", "senior_imessage")
FORWARD_VIA = ("sms", "imessage")
FLAG_TITLES = {
    "AUTHORITY_IMPERSONATION": "claimed authority",
    "URGENCY_PRESSURE": "pressure to act quickly", "THREAT": "threats",
    "SECRECY": "requests for secrecy", "STAY_ON_LINE": "pressure to stay on the line",
    "UNUSUAL_PAYMENT": "unusual payment methods", "SAFE_ACCOUNT_TRANSFER": "account-transfer request",
    "REMOTE_ACCESS": "remote access", "CREDENTIAL_REQUEST": "account-detail request",
    "OTP_REQUEST": "verification-code request", "FAMILY_EMERGENCY": "claimed family emergency",
    "PRIZE_OR_REFUND": "unexpected fee, prize or refund", "VERIFICATION_AVOIDANCE": "avoiding verification",
}


def address(value: str) -> str:
    value = value.strip().casefold()
    if "@" in value:
        return value
    return "".join(c for c in value if c.isdigit()) or value


def forwarded_sms(text: str, sender: str, *, released=False) -> str:
    """What the senior receives. A text that passed screening goes through as it is, with nothing added.
    One the caregiver released after it was held carries a note saying so, and who it came from."""
    if not released:
        return text
    return (f"{text}\n\n— Palisade screening —\nOriginal sender: {sender}\nReleased after caregiver review.\n"
            "Sender identity has not been verified.\nReplies are not forwarded.")


class GatewayMessage(BaseModel):
    message_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    sender: str = Field(min_length=1, max_length=128, pattern=r"^[^\r\n]+$")
    line: str = Field(min_length=1, max_length=128)
    text: str = Field(default="", max_length=10000)
    content_type: Literal["text", "unsupported"] = "text"

    @model_validator(mode="after")
    def valid_content(self):
        if not self.sender.strip() or not address(self.sender):
            raise ValueError("Sender address is required")
        if self.content_type == "text" and not self.text.strip():
            raise ValueError("A text body is required")
        return self


class Decision(BaseModel):
    action: Literal["release", "discard"]


class SendResult(BaseModel):
    attempt_id: str = Field(min_length=1, max_length=128)
    outcome: Literal["sent", "delivery_unknown", "delivery_failed"]


class ClaimRequest(BaseModel):
    kind: Literal["caregiver_alert", "senior_imessage"] = "caregiver_alert"


class Heartbeat(BaseModel):
    connected: bool


class GatewayStore:
    def __init__(self, settings, bus, *, clock=time.time):
        self.settings, self.bus, self.clock = settings, bus, clock
        # Someone has to be let in to review what is held: by the access token, or by signing in.
        if not all((settings.photon_bridge_token, settings.events_token or settings.auth0_domain,
                    settings.twilio_proxy_number, settings.senior_phone, settings.photon_caregiver_address,
                    settings.gateway_encryption_key)):
            raise ValueError("Text gateway requires a bridge token, an access token or sign-in, "
                             "the Twilio number, destinations and an encryption key")
        if settings.photon_bridge_token == settings.events_token:
            raise ValueError("Photon bridge and caregiver tokens must be different")
        if settings.text_forward_via not in FORWARD_VIA:
            raise ValueError("TEXT_FORWARD_VIA must be sms or imessage")
        self.senior_address = settings.senior_imessage_address or settings.senior_phone
        if settings.text_forward_via == "imessage" and address(self.senior_address) in {
            address(settings.twilio_proxy_number), address(settings.photon_caregiver_address),
        }:
            raise ValueError("The senior's iMessage address must differ from the Twilio number and the caregiver")
        if not settings.validate_signature:
            raise ValueError("Text gateway requires Twilio signature validation")
        if len({address(settings.twilio_proxy_number), address(settings.senior_phone),
                address(settings.photon_caregiver_address)}) != 3:
            raise ValueError("Twilio number, senior and caregiver must be distinct addresses")
        if not all(re.fullmatch(r"\+[1-9][0-9]{6,14}", n) for n in
                   (settings.twilio_proxy_number, settings.senior_phone)):
            raise ValueError("Twilio number and SENIOR_PHONE must use E.164 phone numbers")
        if settings.photon_line and address(settings.photon_line) in {
            address(settings.twilio_proxy_number), address(settings.senior_phone),
            address(settings.photon_caregiver_address),
        }:
            raise ValueError("Optional Photon line must differ from Twilio and participant addresses")
        try:
            self.cipher = Fernet(settings.gateway_encryption_key.encode())
        except (ValueError, TypeError):
            raise ValueError("GATEWAY_ENCRYPTION_KEY must be a Fernet key") from None
        directory = settings.log_dir / "gateway"
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
        path = directory / "queue.sqlite3"
        # Pre-create with private permissions before SQLite writes any content.
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        path.chmod(0o600)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA secure_delete=ON")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS messages (
                message_id TEXT PRIMARY KEY, ts REAL NOT NULL, expires_at REAL NOT NULL,
                sender TEXT NOT NULL, payload BLOB, content_type TEXT NOT NULL,
                status TEXT NOT NULL, risk INTEGER, label TEXT, flags TEXT NOT NULL DEFAULT '[]',
                hold_reason TEXT, decision TEXT
            );
            CREATE TABLE IF NOT EXISTS outbox (
                job_id TEXT PRIMARY KEY, message_id TEXT NOT NULL REFERENCES messages(message_id),
                kind TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
                attempt_id TEXT, claimed_at REAL, completed_at REAL,
                UNIQUE(message_id, kind)
            );
            CREATE INDEX IF NOT EXISTS outbox_status ON outbox(status);
        """)
        # Upgrade the earlier Photon queue without losing review records.
        columns = {row["name"] for row in self.db.execute("PRAGMA table_info(messages)")}
        if "source" not in columns:
            self.db.execute("ALTER TABLE messages ADD COLUMN source TEXT NOT NULL DEFAULT 'photon'")
        columns = {row["name"] for row in self.db.execute("PRAGMA table_info(outbox)")}
        if "provider_sid" not in columns:
            self.db.execute("ALTER TABLE outbox ADD COLUMN provider_sid TEXT")
        self.db.commit()
        self.last_heartbeat = 0.0
        self.connected = False
        self.recover_sends()
        # TEXT_FORWARD_VIA may have been changed since a text was queued: it goes out the way it is set now.
        with self.transaction():
            self.db.execute("UPDATE outbox SET kind=? WHERE status='queued' AND kind IN ('relay','senior_imessage') AND kind!=?",
                            (self.relay_kind, self.relay_kind))
        with self.transaction():
            for row in self.db.execute("SELECT message_id FROM messages WHERE source!='twilio' "
                                       "AND status IN ('pending','held','queued')").fetchall():
                self.db.execute("UPDATE messages SET status='held',hold_reason='transport_changed' WHERE message_id=?",
                                (row["message_id"],))
                self.db.execute("UPDATE outbox SET status='cancelled' WHERE message_id=? AND kind IN ('relay','senior_imessage') AND status='queued'",
                                (row["message_id"],))
                self._enqueue(row["message_id"], "caregiver_alert")
        self.cleanup()

    @property
    def relay_kind(self):
        """The outbox kind that carries an approved text to the senior."""
        return "senior_imessage" if self.settings.text_forward_via == "imessage" else "relay"

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def _row(self, message_id):
        row = self.db.execute("SELECT * FROM messages WHERE message_id=?", (message_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Message not found")
        return row

    def summary(self, row):
        flags = json.loads(row["flags"])
        advice = {
            "unsupported_content": "This version checks text only. Attachments have not been analyzed.",
            "analysis_incomplete": "Palisade could not complete analysis and withheld delivery. Review the text before releasing it.",
            "message_too_long": "This text plus the screening note exceeds the size limit. It cannot be released in this version.",
            "transport_changed": "This record came from the previous Photon gateway. It will not be forwarded over SMS; discard it or ask the sender to text the Twilio number.",
        }.get(row["hold_reason"], message_reason(flags, row["risk"] or 0))
        return {
            "message_id": row["message_id"], "ts": row["ts"], "sender": row["sender"],
            "source": row["source"], "delivery_provider": "twilio" if row["source"] == "twilio" else "photon",
            "status": row["status"], "risk": row["risk"],
            "label": row["label"], "flags": flags, "hold_reason": row["hold_reason"],
            "expires_at": row["expires_at"],
            "review_eligible": row["status"] == "held" and row["content_type"] == "text"
                               and row["source"] == "twilio" and row["hold_reason"] not in NONRELEASABLE_REASONS
                               and row["payload"] is not None and row["expires_at"] > self.clock(),
            "explanation": advice,
        }

    def publish(self, message_id):
        summary = self.summary(self._row(message_id))
        event_data = dict(summary)
        # Feed freshness uses event time, even when scoring finishes long after receipt.
        event_data["received_at"] = event_data.pop("ts")
        self.bus.publish("gateway_message_updated", **event_data)
        return summary

    def payload(self, row):
        if row["payload"] is None:
            return {}
        return json.loads(self.cipher.decrypt(row["payload"]))

    def ingest(self, item: GatewayMessage):
        self.cleanup()
        if address(item.line) != address(self.settings.twilio_proxy_number):
            raise HTTPException(400, "Message is not addressed to the Palisade line")
        if address(item.sender) in {address(self.settings.senior_phone), address(self.settings.caregiver_phone),
                                    address(self.settings.photon_caregiver_address),
                                    address(self.settings.twilio_proxy_number)}:
            raise HTTPException(400, "Participant replies are not relay input")
        existing = self.db.execute("SELECT * FROM messages WHERE message_id=?", (item.message_id,)).fetchone()
        if existing:
            return existing
        encrypted = self.cipher.encrypt(json.dumps({"text": item.text if item.content_type == "text" else "",
                                                   "sender": item.sender}).encode())
        now = self.clock()
        with self.transaction():
            self.db.execute("INSERT OR IGNORE INTO messages(message_id,ts,expires_at,sender,payload,content_type,status,source) "
                            "VALUES(?,?,?,?,?,?, 'pending','twilio')",
                            (item.message_id, now, now + TTL, mask_phone(item.sender), encrypted, item.content_type))
        return self._row(item.message_id)

    def finish_screening(self, message_id, result, reason=None):
        self.cleanup()
        row = self._row(message_id)
        if row["status"] != "pending":
            return self.summary(row)
        held = reason is not None or result["risk"] >= 30
        reason = reason or ("scam_signals" if held else None)
        with self.transaction():
            self.db.execute("UPDATE messages SET status=?,risk=?,label=?,flags=?,hold_reason=? WHERE message_id=?",
                            ("held" if held else "queued", result["risk"], result["label"],
                             json.dumps(result["flags"]), reason, message_id))
            self._enqueue(message_id, "caregiver_alert" if held else self.relay_kind)
        return self.publish(message_id)

    def _enqueue(self, message_id, kind):
        self.db.execute("INSERT OR IGNORE INTO outbox(job_id,message_id,kind) VALUES(?,?,?)",
                        (secrets.token_hex(16), message_id, kind))

    def list(self):
        self.cleanup()
        # Never hide held messages behind the recent-activity limit.
        rows = self.db.execute("SELECT * FROM messages WHERE status IN ('held','pending') OR message_id IN "
                               "(SELECT message_id FROM messages ORDER BY ts DESC LIMIT 50) ORDER BY ts DESC").fetchall()
        return [self.summary(row) for row in rows]

    def detail(self, message_id):
        self.cleanup()
        row = self._row(message_id)
        data = self.summary(row)
        data.update(self.payload(row))
        # Keep the masked summary field and explicitly name the private address.
        data["original_sender"] = data.pop("sender", None) if row["payload"] else None
        data["sender"] = row["sender"]
        return data

    def decide(self, message_id, action):
        self.cleanup()
        with self.transaction():
            row = self._row(message_id)
            if row["decision"] == action:
                return self.summary(row)
            if row["status"] != "held":
                raise HTTPException(409, "Message is no longer held for review")
            # Recheck after acquiring the write lock: expiry can pass while waiting.
            if row["expires_at"] <= self.clock():
                raise HTTPException(409, "Message review has expired")
            if action == "release":
                if row["content_type"] != "text" or row["payload"] is None or row["source"] != "twilio" or row["hold_reason"] in NONRELEASABLE_REASONS:
                    raise HTTPException(409, "Unsupported content cannot be released")
                self.db.execute("UPDATE messages SET status='queued',decision='release' WHERE message_id=?", (message_id,))
                self._enqueue(message_id, self.relay_kind)
            else:
                self.db.execute("UPDATE messages SET status='discarded',decision='discard',payload=NULL WHERE message_id=?",
                                (message_id,))
            self.db.execute("UPDATE outbox SET status='cancelled' WHERE message_id=? AND kind='caregiver_alert' "
                            "AND status='queued'", (message_id,))
        return self.publish(message_id)

    def cleanup(self):
        now = self.clock()
        changed = []
        with self.transaction():
            rows = self.db.execute("SELECT message_id,status FROM messages WHERE expires_at<=? AND payload IS NOT NULL",
                                   (now,)).fetchall()
            for row in rows:
                status = "expired" if row["status"] in {"pending", "held", "queued"} else row["status"]
                self.db.execute("UPDATE messages SET payload=NULL,status=? WHERE message_id=?", (status, row["message_id"]))
                self.db.execute("UPDATE outbox SET status='cancelled' WHERE message_id=? AND status='queued'",
                                (row["message_id"],))
                changed.append(row["message_id"])
            stale = self.db.execute("SELECT * FROM outbox WHERE status='sending' AND claimed_at<=?", (now - SEND_LEASE,)).fetchall()
            for job in stale:
                self._unknown(job)
                changed.append(job["message_id"])
        for message_id in set(changed):
            self.publish(message_id)

    def _unknown(self, job):
        self.db.execute("UPDATE outbox SET status='delivery_unknown',completed_at=? WHERE job_id=?",
                        (self.clock(), job["job_id"]))
        if job["kind"] in RELAY_KINDS:
            self.db.execute("UPDATE messages SET status='delivery_unknown' WHERE message_id=?", (job["message_id"],))

    def recover_sends(self):
        with self.transaction():
            for job in self.db.execute("SELECT * FROM outbox WHERE status='sending'").fetchall():
                self._unknown(job)

    def claim(self, kind="caregiver_alert"):
        self.cleanup()
        if kind not in {"relay", "caregiver_alert", "senior_imessage"}:
            raise ValueError("Unsupported outbox kind")
        if kind in RELAY_KINDS:
            # Only the transport the server is set to use may take a text for the senior, and only
            # while forwarding is on and the server is not in practice mode.
            if (not self.settings.text_forwarding_enabled or kind != self.relay_kind
                    or (kind == "senior_imessage" and self.settings.dry_run_actions)):
                return None
        with self.transaction():
            job = self.db.execute("SELECT * FROM outbox WHERE status='queued' AND kind=? ORDER BY rowid LIMIT 1",
                                  (kind,)).fetchone()
            if job is None:
                return None
            row = self._row(job["message_id"])
            if row["expires_at"] <= self.clock():
                self.db.execute("UPDATE messages SET payload=NULL,status=CASE WHEN status IN "
                                "('pending','held','queued') THEN 'expired' ELSE status END WHERE message_id=?",
                                (row["message_id"],))
                self.db.execute("UPDATE outbox SET status='cancelled' WHERE message_id=? AND status='queued'",
                                (row["message_id"],))
                return None
            if job["kind"] in RELAY_KINDS:
                if row["status"] != "queued" or row["payload"] is None or row["source"] != "twilio":
                    self.db.execute("UPDATE outbox SET status='cancelled' WHERE job_id=?", (job["job_id"],))
                    return None
                private = self.payload(row)
                text = forwarded_sms(private["text"], private["sender"], released=row["decision"] == "release")
                if len(text) > MAX_SMS_LENGTH:
                    raise ValueError("Approved message exceeds SMS size limit")
                recipient = self.senior_address if job["kind"] == "senior_imessage" else self.settings.senior_phone
                self.db.execute("UPDATE messages SET status='sending' WHERE message_id=?", (row["message_id"],))
            else:
                flags = json.loads(row["flags"])
                warning = {"unsupported_content": "Unsupported content", "analysis_incomplete": "Analysis incomplete",
                           "message_too_long": "Text exceeds the SMS size limit", "transport_changed": "Previous transport record"}.get(
                    row["hold_reason"], "Likely scam" if (row["risk"] or 0) >= 70 else "Suspicious text")
                categories = ", ".join(FLAG_TITLES[f] for f in flags if f in FLAG_TITLES)
                text = f"Palisade held a text sent to the protected Palisade number: {warning}."
                if categories:
                    text += f" Warning signs: {categories}."
                text += " Open the Palisade app's Texts tab to review it."
                recipient = self.settings.photon_caregiver_address
            attempt = secrets.token_hex(16)
            self.db.execute("UPDATE outbox SET status='sending',attempt_id=?,claimed_at=? WHERE job_id=?",
                            (attempt, self.clock(), job["job_id"]))
        self.publish(row["message_id"])
        return {"job_id": job["job_id"], "message_id": row["message_id"], "kind": job["kind"],
                "attempt_id": attempt, "recipient": recipient,
                "line": self.settings.twilio_proxy_number if kind == "relay" else self.settings.photon_line, "text": text}

    def complete(self, job_id, result: SendResult, *, kind="caregiver_alert", provider_sid=None):
        """`kind` is the transport reporting, or the several kinds it is allowed to report on."""
        allowed = (kind,) if isinstance(kind, str) else tuple(kind)
        with self.transaction():
            job = self.db.execute("SELECT * FROM outbox WHERE job_id=?", (job_id,)).fetchone()
            if job is None:
                raise HTTPException(404, "Job not found")
            if job["kind"] not in allowed:
                raise HTTPException(403, "Wrong delivery transport")
            if not hmac.compare_digest(job["attempt_id"] or "", result.attempt_id):
                raise HTTPException(409, "Stale send attempt")
            if provider_sid and job["provider_sid"] and provider_sid != job["provider_sid"]:
                raise HTTPException(409, "Provider message identity mismatch")
            if job["status"] == result.outcome:
                return self.summary(self._row(job["message_id"]))
            if job["status"] == "sent" and result.outcome == "delivery_unknown":
                return self.summary(self._row(job["message_id"]))
            if job["status"] in {"delivered", "delivery_failed"} and result.outcome in {"sent", "delivery_unknown"}:
                return self.summary(self._row(job["message_id"]))
            # A late acknowledgement can resolve an uncertain send, but never revive a cancelled job.
            if job["status"] not in {"sending", "delivery_unknown"}:
                raise HTTPException(409, "Job is no longer sending")
            self.db.execute("UPDATE outbox SET status=?,completed_at=?,provider_sid=coalesce(provider_sid,?) WHERE job_id=?",
                            (result.outcome, self.clock(), provider_sid, job_id))
            if job["kind"] in RELAY_KINDS:
                self.db.execute("UPDATE messages SET status=?,payload=CASE WHEN ?='sent' THEN NULL ELSE payload END "
                                "WHERE message_id=?", (result.outcome, result.outcome, job["message_id"]))
        return self.publish(job["message_id"])

    def sms_status(self, job_id, attempt_id, form):
        """Called only after the HTTP layer validates the Twilio signature."""
        if (address(form.get("From", "")) != address(self.settings.twilio_proxy_number) or
                address(form.get("To", "")) != address(self.settings.senior_phone) or
                not re.fullmatch(r"(?:SM|MM)[0-9a-fA-F]{32}", form.get("MessageSid", ""))):
            raise HTTPException(400, "Invalid SMS callback identity or destination")
        status = form.get("MessageStatus", "")
        if status not in {"accepted", "queued", "sending", "sent", "delivered", "failed", "undelivered", "canceled"}:
            return
        with self.transaction():
            job = self.db.execute("SELECT * FROM outbox WHERE job_id=? AND kind='relay'", (job_id,)).fetchone()
            if job is None:
                raise HTTPException(404, "Job not found")
            if not hmac.compare_digest(job["attempt_id"] or "", attempt_id):
                raise HTTPException(409, "Stale send attempt")
            if job["provider_sid"] and job["provider_sid"] != form["MessageSid"]:
                raise HTTPException(409, "Provider message identity mismatch")
            if job["status"] in {"delivered", "delivery_failed"}:
                return  # terminal callbacks are idempotent and cannot be downgraded by replay
            if job["status"] not in {"sending", "sent", "delivery_unknown"}:
                raise HTTPException(409, "Job is not a send attempt")
            outcome = "delivered" if status == "delivered" else (
                "delivery_failed" if status in {"failed", "undelivered", "canceled"} else "sent")
            self.db.execute("UPDATE outbox SET status=?,provider_sid=?,completed_at=? WHERE job_id=?",
                            (outcome, form["MessageSid"], self.clock(), job_id))
            self.db.execute("UPDATE messages SET status=?,payload=NULL WHERE message_id=?",
                            (outcome, job["message_id"]))
        self.publish(job["message_id"])

    def health(self):
        counts = {row["status"]: row["n"] for row in self.db.execute("SELECT status,count(*) n FROM outbox GROUP BY status")}
        sent = self.db.execute("SELECT max(completed_at) FROM outbox WHERE status IN ('sent','delivered')").fetchone()[0]
        connected = self.connected and self.clock() - self.last_heartbeat < 45
        sms_ready = not self.settings.dry_run_actions and bool(self.settings.twilio_account_sid and self.settings.twilio_auth_token)
        via = self.settings.text_forward_via
        return {"enabled": True, "forwarding_enabled": self.settings.text_forwarding_enabled,
                "forward_via": via,
                "sms_ready": sms_ready,
                # Whether an approved text could be sent on right now, by whichever way the server is set to.
                "forwarding_ready": sms_ready if via == "sms" else (not self.settings.dry_run_actions and connected),
                "ingress": "twilio", "delivery_provider": "photon" if via == "imessage" else "twilio",
                "connected": connected,
                "queue_counts": counts, "last_successful_send": sent}


class GatewayService:
    def __init__(self, settings, bus, scorer, *, client_factory=None, public_host=lambda: ""):
        self.store = GatewayStore(settings, bus)
        self.scorer = scorer
        self.requires_llm = settings.scorer_model.lower() not in {"rules", "none"}
        self.lock = asyncio.Lock()
        self.tasks = []
        self.wake = asyncio.Event()
        from gateway_sms import SMSDelivery
        self.delivery = SMSDelivery(self.store, client_factory=client_factory, public_host=public_host)

    async def screen(self, message_id):
        row = self.store._row(message_id)
        if row["status"] != "pending":
            return self.store.summary(row)
        empty = {"risk": None, "label": None, "flags": []}
        if row["content_type"] != "text":
            return self.store.finish_screening(message_id, empty, "unsupported_content")
        try:
            private = self.store.payload(row)
        except Exception:
            return self.store.finish_screening(message_id, empty, "analysis_incomplete")
        if len(forwarded_sms(private["text"], private["sender"])) > MAX_SMS_LENGTH:
            return self.store.finish_screening(message_id, empty, "message_too_long")
        if self.requires_llm and self.scorer.llm is None:
            return self.store.finish_screening(message_id, empty, "analysis_incomplete")
        try:
            result = await self.scorer.evaluate(self.store.payload(row)["text"])
            reason = "analysis_incomplete" if result.get("llm_error") else None
        except Exception:
            # No exception string: SDK/model exceptions may contain submitted text.
            result, reason = empty, "analysis_incomplete"
        # A held text may be released, and a released one carries the note: if that does not fit, it can only be discarded.
        held = reason is not None or (result["risk"] or 0) >= 30
        if held and len(forwarded_sms(private["text"], private["sender"], released=True)) > MAX_SMS_LENGTH:
            reason = "message_too_long"
        return self.store.finish_screening(message_id, result, reason)

    async def ingest(self, item):
        async with self.lock:
            row = self.store.ingest(item)
            return await self.screen(row["message_id"])

    def accept(self, item):
        # Commit before acknowledging Twilio; scoring is recovered after a restart.
        row = self.store.ingest(item)
        self.wake.set()
        return self.store.summary(row)

    async def maintain(self):
        while True:
            self.wake.clear()
            self.store.cleanup()
            async with self.lock:
                pending = self.store.db.execute("SELECT message_id FROM messages WHERE status='pending'").fetchall()
                for row in pending:
                    await self.screen(row["message_id"])
            # Accepts during scoring keep the event set and trigger another immediate scan.
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=60)
            except TimeoutError:
                pass

    async def expire(self):
        while True:
            self.store.cleanup()
            await asyncio.sleep(60)

    def start(self):
        self.tasks = [asyncio.create_task(self.maintain()), asyncio.create_task(self.expire()),
                      asyncio.create_task(self.delivery.run())]

    async def close(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.store.close()


def gateway_router(service, settings, authorize):
    """The caregiver's routes and the Photon bridge's. `authorize` is the server's own check of who may watch."""

    def no_cache(response: Response):
        response.headers["Cache-Control"] = "no-store"

    router = APIRouter(dependencies=[Depends(no_cache)])

    def auth(request, token):
        supplied = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if not token or not hmac.compare_digest(supplied.encode(), token.encode()):
            raise HTTPException(401, "Missing or wrong token")
        if service is None:
            raise HTTPException(503, "Text gateway is disabled")

    async def caregiver(request):
        """Whoever may watch calls may read and decide on held texts: the access token, or a signed-in caregiver."""
        await authorize(request, header_only=True)
        if service is None:
            raise HTTPException(503, "Text gateway is disabled")

    @router.get("/gateway/messages")
    async def messages(request: Request):
        await caregiver(request)
        return {"messages": service.store.list(), "shield_number": settings.twilio_proxy_number,
                "forwarding_enabled": settings.text_forwarding_enabled}

    @router.get("/gateway/messages/{message_id}")
    async def detail(request: Request, message_id: str):
        await caregiver(request)
        return service.store.detail(message_id)

    @router.post("/gateway/messages/{message_id}/decision")
    async def decision(request: Request, message_id: str, item: Decision):
        await caregiver(request)
        return service.store.decide(message_id, item.action)

    @router.post("/integrations/photon/outbox/claim")
    async def claim(request: Request, item: ClaimRequest = ClaimRequest()):
        auth(request, settings.photon_bridge_token)
        return {"job": service.store.claim(item.kind)}

    @router.post("/integrations/photon/outbox/{job_id}/result")
    async def result(request: Request, job_id: str, item: SendResult):
        auth(request, settings.photon_bridge_token)
        return service.store.complete(job_id, item, kind=PHOTON_JOB_KINDS)

    @router.post("/integrations/photon/heartbeat")
    async def heartbeat(request: Request, item: Heartbeat):
        auth(request, settings.photon_bridge_token)
        service.store.connected = item.connected
        service.store.last_heartbeat = service.store.clock()
        return {"ok": True}

    @router.get("/integrations/photon/status")
    async def status(request: Request):
        auth(request, settings.photon_bridge_token)
        held = [m for m in service.store.list() if m["status"] == "held"]
        latest = held[0] if held else None
        if latest:
            reason = latest["hold_reason"]
            why = {"analysis_incomplete": "Analysis could not finish, so Palisade withheld delivery.",
                   "unsupported_content": "This version checks text only; the content could not be analyzed.",
                   "message_too_long": "The original text and screening note exceed the size limit.",
                   "transport_changed": "This record belongs to the previous Photon gateway and will not be forwarded over SMS."}.get(reason)
            if not why:
                categories = ", ".join(FLAG_TITLES[f] for f in latest["flags"] if f in FLAG_TITLES)
                why = f"Palisade detected: {categories}." if categories else "The risk score reached the review threshold."
                why += " " + latest["explanation"]
        else:
            why = "There are no messages awaiting review."
        return {**service.store.health(), "held_count": len(held), "why": why,
                "shield_number": settings.twilio_proxy_number}

    return router
