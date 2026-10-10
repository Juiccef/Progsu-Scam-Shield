"""Palisade backend: Twilio webhooks, the media stream, and the event feed.

    uvicorn main:app --port 8000 --timeout-graceful-shutdown 2
"""

from __future__ import annotations

import asyncio
import base64
import dataclasses
import hmac
import json
import logging
import re
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qsl

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field, ValidationError, field_validator
from twilio.request_validator import RequestValidator
from twilio.twiml.voice_response import VoiceResponse

import demo
from actions import Actions
from auth import Auth0, SignInError
from config import ROOT, Settings, get_settings
from events import EventBus, mask_phone
from history import CallHistory
from messages import MessageHistory
from gateway import GatewayMessage, GatewayService, address, gateway_router
from scorer.decision import is_allowlisted, normalize_phone
from scorer.flags import CALLER, SENIOR
from scorer.llm import LLMScorer, ScorerPrompt
from scorer.messages import MessageScorer
from session import CallRegistry, CallSession
from stt import STTFactory, STTResult, STTStream, make_stt_factory

log = logging.getLogger("shield")

DISCLOSURE = "This call is monitored by Palisade, a scam protection service."
GOODBYE = "Goodbye."
FINISHED = {"completed", "busy", "no-answer", "failed", "canceled"}
MEDIA_REPORT_EVERY_S = 5.0
DEMO_SCRIPTS = ROOT / "eval" / "eval-scripts.json"
_DEFAULT = object()


class MessageInput(BaseModel):
    text: str = Field(min_length=1, max_length=10000)
    sender: str = Field(default="", max_length=128)

    @field_validator("text")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Message text cannot be blank")
        return value


def build_llm(settings: Settings) -> Optional[LLMScorer]:
    if settings.scorer_model.lower() in ("rules", "none"):
        return None
    try:
        return LLMScorer(
            settings.scorer_model,
            ScorerPrompt.load(settings.scorer_prompt),
            timeout_s=settings.llm_timeout_s,
            early_exit=settings.llm_early_exit,
        )
    except Exception as exc:  # unknown provider, missing key, unreadable prompt
        log.error("LLM scorer is off, running on rules only: %s", exc)
        return None


def create_app(
    settings: Optional[Settings] = None,
    *,
    stt_factory=_DEFAULT,
    llm=_DEFAULT,
    message_llm=_DEFAULT,
    twilio_client_factory=None,
    sign_in=_DEFAULT,
) -> FastAPI:
    settings = settings or get_settings()
    stt: Optional[STTFactory] = make_stt_factory(settings) if stt_factory is _DEFAULT else stt_factory
    scorer_llm: Optional[LLMScorer] = build_llm(settings) if llm is _DEFAULT else llm
    sms_llm = (
        build_llm(dataclasses.replace(settings, scorer_prompt=ROOT / "prompts/message-scorer-prompt.md", llm_early_exit=False))
        if message_llm is _DEFAULT else message_llm
    )
    message_scorer = MessageScorer(sms_llm)
    history = CallHistory.from_log(settings.log_dir / "events.jsonl")
    message_history = MessageHistory.from_log(settings.log_dir / "events.jsonl")
    bus = EventBus(settings.log_dir)
    bus.add_listener(history.record)
    bus.add_listener(message_history.record)
    message_lock = asyncio.Lock()
    state = {"public_host": settings.public_host, "last_action": None, "actions": {}, "demo_task": None}
    # Text protection is extra. A wrong setting for it, or a queue that will not open, turns
    # texts off and says why. It must never keep the server from starting: calls come first.
    gateway, texts_off_because = None, None
    if settings.text_gateway_enabled:
        try:
            gateway = GatewayService(
                settings, bus, message_scorer, client_factory=twilio_client_factory, public_host=lambda: state["public_host"]
            )
        except ValueError as wrong:  # GatewayStore's own sentences about a setting; they hold no secrets
            texts_off_because = str(wrong)
        except (OSError, sqlite3.Error) as broken:
            texts_off_because = f"The text queue could not be opened ({type(broken).__name__})"
        if texts_off_because:
            log.error("Text protection is OFF: %s", texts_off_because)

    def remember_action(metrics: dict) -> None:
        state["last_action"] = metrics
        state["actions"][metrics["kind"]] = metrics

    actions = Actions(
        settings, bus, client_factory=twilio_client_factory, public_host=lambda: state["public_host"]
    )
    # Demo calls do not exist at Twilio, so their actions are always dry-run and they
    # never overwrite the latency of the last real action in /metrics/last.
    demo_actions = Actions(dataclasses.replace(settings, dry_run_actions=True), bus)
    registry = CallRegistry(
        lambda call_sid, caller, monitored, simulated: CallSession(
            call_sid,
            settings=settings,
            bus=bus,
            actions=demo_actions if simulated else actions,
            llm=scorer_llm,
            caller=caller,
            monitored=monitored,
            simulated=simulated,
            on_action=None if simulated else remember_action,
        )
    )
    voice = {"voice": settings.say_voice} if settings.say_voice else {}
    if sign_in is _DEFAULT:
        sign_in = (
            Auth0(settings.auth0_domain, settings.auth0_audience, settings.caregiver_emails)
            if settings.auth0_domain
            else None
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if gateway:
            gateway.start()
        log.info(
            "Palisade ready: scorer=%s stt=%s senior=%s caregiver=%s public_host=%s actions=%s signature_check=%s texts=%s",
            scorer_llm.model if scorer_llm else "rules only",
            settings.stt_provider if stt else "none (audio is counted, not transcribed)",
            mask_phone(settings.senior_phone),
            mask_phone(settings.caregiver_phone),
            settings.public_host or "(from request headers)",
            "live" if actions.live else "dry run",
            "on" if settings.validate_signature else "OFF",
            "OFF (a setting is wrong)" if texts_off_because
            else "off" if gateway is None
            else "on" if settings.text_forwarding_enabled
            else "on, forwarding paused",
        )
        yield
        if gateway:
            await gateway.close()
        if state["demo_task"] is not None:
            state["demo_task"].cancel()
        await registry.close_all()
        if scorer_llm is not None and llm is _DEFAULT:
            await scorer_llm.aclose()
        if sms_llm is not None and message_llm is _DEFAULT:
            await sms_llm.aclose()
        if sign_in is not None:
            await sign_in.aclose()
        bus.close()

    app = FastAPI(title="Palisade", lifespan=lifespan)
    app.state.gateway = gateway
    app.state.bus = bus
    app.state.registry = registry
    app.state.history = history
    app.state.message_history = message_history

    # --- helpers ---------------------------------------------------------

    def public_host(request: Request) -> str:
        return settings.public_host or request.headers.get("x-forwarded-host") or request.headers.get("host", "")

    async def twilio_form(request: Request) -> dict[str, str]:
        """Parse a Twilio webhook and check its X-Twilio-Signature."""
        form = dict(parse_qsl((await request.body()).decode("utf-8", "replace"), keep_blank_values=True))
        if not settings.validate_signature:
            state["public_host"] = public_host(request)
            return form
        if not settings.twilio_auth_token:
            log.error("Rejected %s: TWILIO_AUTH_TOKEN is not set, so the signature cannot be checked", request.url.path)
            raise HTTPException(403, "Twilio signature validation is not configured")
        scheme = "https" if settings.public_host else request.headers.get("x-forwarded-proto", request.url.scheme)
        url = f"{scheme}://{public_host(request)}{request.url.path}"
        if request.url.query:
            url += f"?{request.url.query}"
        signature = request.headers.get("x-twilio-signature", "")
        if not RequestValidator(settings.twilio_auth_token).validate(url, form, signature):
            log.warning(
                "Rejected %s: bad Twilio signature (checked against %s; PUBLIC_HOST must match the webhook URL in Twilio)",
                request.url.path,
                url,
            )
            raise HTTPException(403, "Invalid Twilio signature")
        state["public_host"] = public_host(request)  # where Twilio reaches us, for callback URLs
        return form

    async def authorize(request: Request, *, header_only: bool = False) -> None:
        """/events carries live transcripts. A signed-in caregiver may read it, and so may EVENTS_TOKEN where set.

        `header_only` is for a held text, which is worth more than a transcript line going by: the
        token is then taken from the Authorization header alone, never from a URL, where it gets logged.
        """
        bearer = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        supplied = bearer if header_only else request.query_params.get("token") or bearer
        if settings.events_token and hmac.compare_digest(supplied.encode(), settings.events_token.encode()):
            return
        if sign_in is None:
            if settings.events_token:
                raise HTTPException(401, "Missing or wrong token")
            return
        try:
            # Only from the Authorization header: a sign-in token never belongs in a URL, where it gets logged.
            await sign_in.caregiver(bearer)
        except SignInError as refused:
            raise HTTPException(refused.status, str(refused)) from refused

    def twiml(response: VoiceResponse) -> Response:
        return Response(str(response), media_type="application/xml")

    # Whoever may watch calls may read and decide on held texts: the same check, header only.
    app.include_router(gateway_router(gateway, settings, authorize))

    # --- Twilio webhooks ---------------------------------------------------

    async def analyze_message(text: str, sender: str, message_id: str, source: str, *, text_only: bool = False) -> dict:
        result = await message_scorer.evaluate(text)
        # Deliberately exclude the body, URLs and model evidence from events/history.
        # Fixed advice can be reconstructed by the client; events.py strips explanation on disk.
        return bus.publish(
            "message_scored", message_id=message_id, sender=mask_phone(sender),
            source=source, text_only=text_only, **result,
        )

    @app.post("/messages/analyze")
    async def message_analyze(request: Request, body: MessageInput) -> dict:
        await authorize(request)
        return await analyze_message(body.text, body.sender, "manual_" + secrets.token_hex(16), "manual")

    @app.get("/messages")
    async def message_list(request: Request) -> dict:
        await authorize(request)
        return {"messages": message_history.list()}

    @app.post("/sms/incoming")
    async def sms_incoming(request: Request) -> Response:
        form = await twilio_form(request)
        if not settings.twilio_proxy_number or normalize_phone(form.get("To", "")) != normalize_phone(settings.twilio_proxy_number):
            raise HTTPException(400, "Message is not addressed to the Palisade proxy number")
        message_id = form.get("MessageSid", "")
        text = form.get("Body", "")
        if not message_id or len(message_id) > 128:
            raise HTTPException(400, "Missing or invalid MessageSid")
        if gateway:
            # Sender-number echoes and participant replies cannot loop into deliveries.
            if not form.get("From", "").strip():
                raise HTTPException(400, "Missing sender address")
            if address(form.get("From", "")) in {
                address(settings.twilio_proxy_number), address(settings.senior_phone),
                address(settings.caregiver_phone), address(settings.photon_caregiver_address),
            }:
                return Response("<Response></Response>", media_type="application/xml")
            if not re.fullmatch(r"(?:SM|MM)[0-9a-fA-F]{32}", message_id):
                raise HTTPException(400, "Invalid MessageSid")
            media_count = form.get("NumMedia", "0")
            if len(media_count) > 6 or not media_count.isascii() or not media_count.isdigit():
                raise HTTPException(400, "Invalid media count")
            try:
                item = GatewayMessage(message_id="twilio_" + message_id, sender=form.get("From", ""),
                                      line=form["To"], text=text,
                                      content_type="unsupported" if int(media_count) > 0 else "text")
            except ValidationError:
                raise HTTPException(400, "Invalid text message content") from None
            gateway.accept(item)
            return Response("<Response></Response>", media_type="application/xml")
        if not text.strip() or len(text) > 10000:
            raise HTTPException(400, "A text body of 1-10000 characters is required; media-only MMS is not supported")
        # Serializes duplicate deliveries; bounded persistent summaries also cover recent retries after restart.
        async with message_lock:
            if not message_history.contains(message_id):
                await analyze_message(
                    text, form.get("From", ""), message_id, "twilio",
                    text_only=form.get("NumMedia", "0") != "0",
                )
        # Never reply to the sender or forward the original content.
        return Response("<Response></Response>", media_type="application/xml")

    @app.post("/gateway/sms/status/{job_id}/{attempt_id}")
    async def gateway_sms_status(request: Request, job_id: str, attempt_id: str) -> Response:
        form = await twilio_form(request)
        if gateway is None:
            raise HTTPException(503, "Text gateway is disabled")
        gateway.store.sms_status(job_id, attempt_id, form)
        return Response(status_code=204)

    @app.post("/voice/incoming")
    async def voice_incoming(request: Request) -> Response:
        """A call reached the proxy number: disclose, start the audio stream, ring the senior."""
        form = await twilio_form(request)
        call_sid, caller = form.get("CallSid", ""), form.get("From", "")
        host = public_host(request)
        response = VoiceResponse()
        if not settings.senior_phone:
            log.error("SENIOR_PHONE is not set, so call %s cannot be connected", call_sid)
            response.say("Palisade is not set up yet. Goodbye.", **voice)
            response.hangup()
            return twiml(response)

        monitored = not is_allowlisted(caller, settings.allowlist)
        session = registry.open(call_sid, caller, monitored)
        bus.publish("call_started", call_sid=call_sid, caller=session.caller, monitored=monitored, simulated=False)
        log.info("incoming call=%s from=%s monitored=%s", call_sid, session.caller, monitored)
        if monitored:
            response.say(DISCLOSURE, **voice)
            stream = response.start().stream(url=f"wss://{host}/media", track="both_tracks")
            stream.parameter(name="token", value=session.token)
        dial_options = {"caller_id": settings.dial_caller_id} if settings.dial_caller_id else {}
        dial = response.dial(timeout=settings.dial_timeout_s, **dial_options)
        dial.number(
            settings.senior_phone,
            status_callback=f"https://{host}/voice/status",
            status_callback_event="initiated ringing answered completed",
            status_callback_method="POST",
        )
        # Reached when the senior's leg ends, including when Palisade ends it.
        response.say(GOODBYE, **voice)
        response.hangup()
        return twiml(response)

    @app.post("/voice/status")
    async def voice_status(request: Request) -> Response:
        """Status callbacks. The senior's leg reports here with ParentCallSid set to the caller's leg."""
        form = await twilio_form(request)
        sid, parent, status = form.get("CallSid", ""), form.get("ParentCallSid", ""), form.get("CallStatus", "")
        log.info("status leg=%s parent=%s status=%s", sid, parent or "-", status)
        session = registry.get(parent or sid)
        if session is None:
            return Response(status_code=204)
        if parent:
            session.child_sid = sid
            if status == "in-progress":
                session.mark_answered()
            elif status in FINISHED and not session.streaming:
                await registry.close(parent, f"senior leg {status}")
        elif status in FINISHED:
            await registry.close(sid, f"call {status}")
        return Response(status_code=204)

    @app.post("/sms/status")
    async def sms_status(request: Request) -> Response:
        """Delivery reports for caregiver texts. A blocked SMS shows up here, not at send time."""
        form = await twilio_form(request)
        status, error = form.get("MessageStatus", ""), form.get("ErrorCode") or None
        bus.publish("sms_status", status=status, error_code=error, to=mask_phone(form.get("To", "")))
        log.log(logging.WARNING if error else logging.INFO, "caregiver SMS status=%s error=%s", status, error)
        return Response(status_code=204)

    # --- Twilio Media Stream -------------------------------------------------

    @app.websocket("/media")
    async def media(ws: WebSocket) -> None:
        await ws.accept()
        session: Optional[CallSession] = None
        streams: dict[str, STTStream] = {}
        frames: dict[str, int] = {}
        last_report = time.time()
        try:
            while True:
                message = json.loads(await ws.receive_text())
                event = message.get("event")
                if event == "media":
                    if session is None:
                        continue
                    payload = message["media"]
                    track = payload.get("track", "inbound")
                    if track not in frames:
                        log.info("media first frame call=%s track=%s", session.call_sid, track)
                    frames[track] = frames.get(track, 0) + 1
                    if track not in streams and stt is not None:
                        streams[track] = await open_track(session, track)
                    if track in streams:
                        streams[track].send_audio(base64.b64decode(payload["payload"]))
                    if time.time() - last_report >= MEDIA_REPORT_EVERY_S:
                        last_report = time.time()
                        log.info("media frames call=%s %s", session.call_sid, frames)
                        bus.publish("media", call_sid=session.call_sid, frames=dict(frames))
                elif event == "start":
                    session = accept_stream(ws, message.get("start") or {})
                    if session is None:
                        await ws.close(code=1008)
                        return
                    streams = await open_stt(session, (message.get("start") or {}).get("tracks") or [])
                elif event == "stop":
                    break
        except WebSocketDisconnect:
            pass
        except Exception:
            log.exception("Media stream handler failed")
        finally:
            for stream in streams.values():
                await stream.close()
            if session is not None:
                log.info("media stream ended call=%s frames=%s", session.call_sid, frames)
                await registry.close(session.call_sid, "stream ended")

    def accept_stream(ws: WebSocket, start: dict) -> Optional[CallSession]:
        """Match the stream to a call we answered. The token only exists in TwiML we generated."""
        call_sid = start.get("callSid", "")
        token = (start.get("customParameters") or {}).get("token", "")
        session = registry.get(call_sid)
        if session is None or not hmac.compare_digest(session.token.encode(), token.encode()):
            log.warning("Rejected a media stream for unknown call %s", call_sid)
            return None
        log.info(
            "media stream started call=%s tracks=%s format=%s signature=%s",
            call_sid,
            start.get("tracks"),
            start.get("mediaFormat"),
            websocket_signature(ws),
        )
        bus.publish("stream_started", call_sid=call_sid, tracks=start.get("tracks"))
        session.begin_stream()
        return session

    def websocket_signature(ws: WebSocket) -> str:
        """Twilio also signs the WebSocket handshake. Logged only: the token check above is what gates."""
        signature = ws.headers.get("x-twilio-signature")
        if not signature:
            return "absent"
        if not settings.twilio_auth_token:
            return "unchecked"
        url = f"wss://{state['public_host'] or ws.headers.get('host', '')}{ws.url.path}"
        return "valid" if RequestValidator(settings.twilio_auth_token).validate(url, {}, signature) else "mismatch"

    async def open_stt(session: CallSession, tracks: list[str]) -> dict[str, STTStream]:
        if stt is None:
            log.warning("No speech-to-text is configured (set DEEPGRAM_API_KEY): audio is counted, not transcribed")
            bus.publish("stt_error", call_sid=session.call_sid, error="speech-to-text is not configured")
            return {}
        return {track: await open_track(session, track) for track in tracks}

    async def open_track(session: CallSession, track: str) -> STTStream:
        speaker = CALLER if track == settings.caller_track else SENIOR

        def on_result(result: STTResult) -> None:
            session.add_transcript(
                speaker, result.text, final=result.is_final, start_at=result.start_at, end_at=result.end_at
            )

        def on_error(error: str) -> None:
            bus.publish("stt_error", call_sid=session.call_sid, track=track, error=error)

        stream = stt(track, on_result, on_error)
        await stream.start()
        return stream

    # --- event feed and metrics ------------------------------------------------

    @app.get("/events")
    async def events(request: Request, replay: bool = True) -> StreamingResponse:
        """Server-sent events: call_started, transcript, score, action, action_result, call_ended, ..."""
        await authorize(request)
        queue = bus.subscribe()
        backlog = bus.recent() if replay else []

        async def feed():
            try:
                # Some clients (URLSession on iOS) do not report the connection as open until
                # the first bytes of the body arrive, so send a comment line straight away.
                yield ": connected\n\n"
                for event in backlog:
                    yield _sse(event)
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15)
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    yield _sse(event)
            finally:
                bus.unsubscribe(queue)

        return StreamingResponse(
            feed(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        )

    @app.get("/metrics/last")
    async def metrics_last(request: Request) -> dict:
        """Seconds from the triggering utterance to the most recent action, with a per-stage breakdown."""
        await authorize(request)
        last = state["last_action"]
        return {
            "seconds": last["seconds"] if last else None,
            "last_action": last,
            "last_warn": state["actions"].get("WARN"),
            "last_end": state["actions"].get("END"),
        }

    @app.get("/calls")
    async def calls(request: Request) -> dict:
        """Recent calls, newest first: outcome, flags, actions and the risk timeline. Never transcript text."""
        await authorize(request)
        return {"calls": history.list()}

    # --- demo calls (DEMO_MODE=true) ---------------------------------------------

    @app.get("/demo/scripts")
    async def demo_scripts(request: Request) -> dict:
        """The scripts a demo call can play. Empty when demo mode is off, so a client can hide the feature."""
        await authorize(request)
        if not settings.demo_mode:
            return {"enabled": False, "scripts": []}
        return {"enabled": True, "scripts": [demo.describe(script) for script in demo.load_scripts(DEMO_SCRIPTS)]}

    @app.post("/demo/call", status_code=202)
    async def demo_call(request: Request, script: str, speed: float = 1.0) -> dict:
        """Start a scripted call with no phone involved. Same scoring and decisions, dry-run actions."""
        await authorize(request)
        if not settings.demo_mode:
            raise HTTPException(404, "Demo mode is off (set DEMO_MODE=true)")
        if registry.any_simulated():
            raise HTTPException(409, "A demo call is already running")
        matches = [candidate for candidate in demo.load_scripts(DEMO_SCRIPTS) if script in candidate["id"]]
        if len(matches) != 1:
            raise HTTPException(404, f"{script!r} matches {len(matches)} scripts")
        call_sid = "CAdemo" + secrets.token_hex(13)
        session = registry.open(call_sid, demo.DEMO_CALLER, monitored=True, simulated=True)
        bus.publish("call_started", call_sid=call_sid, caller=session.caller, monitored=True, simulated=True)
        log.info("demo call=%s script=%s speed=%s", call_sid, matches[0]["id"], speed)
        bus.publish("stream_started", call_sid=call_sid, tracks=["inbound", "outbound"])
        session.begin_stream()
        session.mark_answered()
        state["demo_task"] = demo.start(
            session,
            matches[0],
            speed=min(max(speed, 0.5), 3.0),
            on_finished=lambda: registry.close(call_sid, "demo finished"),
        )
        return {"call_sid": call_sid, "script": matches[0]["id"]}

    @app.get("/auth/config")
    async def auth_config() -> dict:
        """What the dashboard and the app need to send someone to sign in. None of it is secret."""
        if sign_in is None:
            return {"enabled": False}
        return {
            "enabled": True,
            "domain": settings.auth0_domain,
            "audience": settings.auth0_audience,
            "web_client_id": settings.auth0_web_client_id,
            "ios_client_id": settings.auth0_ios_client_id,
        }

    @app.get("/health")
    async def health() -> dict:
        return {
            "ok": True,
            "scorer_model": scorer_llm.model if scorer_llm else "rules",
            "stt": settings.stt_provider if stt else None,
            "twilio_configured": bool(settings.twilio_account_sid and settings.twilio_auth_token),
            "senior_phone_set": bool(settings.senior_phone),
            "caregiver_phone_set": bool(settings.caregiver_phone),
            "actions": "live" if actions.live else "dry_run",
            "signature_validation": settings.validate_signature,
            "demo_mode": settings.demo_mode,
            "warn_threshold": settings.decision.warn_threshold,
            "end_threshold": settings.decision.end_threshold,
            "active_calls": len(registry),
            "message_scorer_model": sms_llm.model if sms_llm else "rules",
            # `error` is there only when text protection was asked for and could not be started.
            "gateway": gateway.store.health()
            if gateway
            else {"enabled": False, **({"error": texts_off_because} if texts_off_because else {})},
        }

    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard() -> HTMLResponse:
        """Temporary single-page view of the event feed. The page holds no data; /events does (and needs ?token=)."""
        return HTMLResponse((Path(__file__).parent / "dashboard.html").read_text(encoding="utf-8"))

    @app.get("/")
    async def root() -> dict:
        return {
            "service": "Palisade",
            "endpoints": ["/dashboard", "/health", "/events", "/calls", "/messages", "/messages/analyze", "/metrics/last"],
        }

    return app


def _sse(event: dict) -> str:
    return f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
for noisy in ("httpx", "httpx2", "httpcore", "twilio.http_client", "anthropic"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

app = create_app()
