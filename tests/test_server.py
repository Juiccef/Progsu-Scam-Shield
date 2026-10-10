"""The HTTP and WebSocket surface, driven the way Twilio drives it. No network is used."""

import base64
import json
import re
import time

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from twilio.request_validator import RequestValidator

from config import Settings
from main import build_llm, create_app
from stt import STTResult

AUTH_TOKEN = "test-auth-token"
HOST = "shield.example.test"
SENIOR = "+15555550101"
SILENCE = base64.b64encode(b"\xff" * 160).decode()


def make_settings(tmp_path, **overrides) -> Settings:
    env = {
        "TWILIO_ACCOUNT_SID": "ACtest",
        "TWILIO_AUTH_TOKEN": AUTH_TOKEN,
        "TWILIO_PROXY_NUMBER": "+15555550100",
        "SENIOR_PHONE": SENIOR,
        "CAREGIVER_PHONE": "+15555550102",
        "PUBLIC_HOST": HOST,
        "SCORER_MODEL": "rules",
        "SAY_VOICE": "default",
        "DRY_RUN_ACTIONS": "true",
        "GRACE_PERIOD_S": "0",
        "SCORE_INTERVAL_S": "0.05",
        "SCORE_MIN_GAP_S": "0",
        "LOG_DIR": str(tmp_path / "logs"),
    }
    env.update(overrides)
    return Settings.from_env(env)


class ScriptedSTT:
    """Speech-to-text stand-in: emits the next scripted line each time `every` audio frames arrive."""

    def __init__(self, lines, on_result, every=5):
        self.lines, self.on_result, self.every = list(lines), on_result, every
        self.frames = 0
        self.closed = False

    async def start(self):
        pass

    def send_audio(self, chunk):
        assert chunk == b"\xff" * 160
        self.frames += 1
        if self.frames % self.every == 0 and self.lines:
            now = time.time()
            self.on_result(STTResult(self.lines.pop(0), True, now - 1.0, now - 0.2))

    async def close(self):
        self.closed = True


SCAM_LINES = {
    "inbound": [
        "This is Officer Daniels. There is a warrant for your arrest, stay on the line.",
        "Do not tell your family. Go and buy gift cards and read me the numbers on the back.",
    ],
    "outbound": ["Oh my. What do I need to do?"],
}


@pytest.fixture
def scripted_stt():
    created = {}

    def factory(track, on_result, on_error):
        created[track] = ScriptedSTT(SCAM_LINES.get(track, []), on_result)
        return created[track]

    factory.created = created
    return factory


def signed_post(client, path, params, token=AUTH_TOKEN):
    signature = RequestValidator(token).compute_signature(f"https://{HOST}{path}", params)
    return client.post(path, data=params, headers={"X-Twilio-Signature": signature})


def incoming(client, call_sid="CAparent", caller="+15555550199"):
    return signed_post(client, "/voice/incoming", {"CallSid": call_sid, "From": caller, "To": "+15555550100"})


def stream_token(twiml: str) -> str:
    return re.search(r'<Parameter name="token" value="([^"]+)"', twiml).group(1)


def frame(track):
    return json.dumps({"event": "media", "media": {"track": track, "payload": SILENCE}})


def start_message(call_sid, token):
    return json.dumps(
        {
            "event": "start",
            "start": {
                "callSid": call_sid,
                "streamSid": "MZtest",
                "tracks": ["inbound", "outbound"],
                "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1},
                "customParameters": {"token": token},
            },
        }
    )


def events_of(app, kind):
    return [event for event in app.state.bus.recent() if event["type"] == kind]


def wait_for(condition, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


def test_dashboard_page_is_served_without_data(tmp_path):
    with TestClient(create_app(make_settings(tmp_path), stt_factory=None)) as client:
        page = client.get("/dashboard")
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    assert "EventSource" in page.text and AUTH_TOKEN not in page.text and SENIOR not in page.text


# --- webhooks ----------------------------------------------------------------


def test_health(tmp_path):
    with TestClient(create_app(make_settings(tmp_path), stt_factory=None)) as client:
        body = client.get("/health").json()
    assert body["ok"] is True
    assert body["scorer_model"] == "rules" and body["stt"] is None and body["actions"] == "dry_run"
    assert AUTH_TOKEN not in json.dumps(body) and SENIOR not in json.dumps(body)


def test_jev_is_announced_as_experimental_and_falls_back_without_its_key(tmp_path, monkeypatch, caplog):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    settings = make_settings(tmp_path, SCORER_MODEL="jev-latest")
    assert build_llm(settings) is None and "needs TYPESAFE_API_KEY" in caplog.text
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-test")
    caplog.clear()
    assert build_llm(settings).model == "jev-latest"
    assert "jev-latest is experimental" in caplog.text


def test_incoming_call_gets_disclosure_stream_and_dial(tmp_path):
    with TestClient(create_app(make_settings(tmp_path), stt_factory=None)) as client:
        response = incoming(client)
    twiml = response.text
    assert response.status_code == 200 and response.headers["content-type"].startswith("application/xml")
    assert "This call is monitored by Palisade, a scam protection service." in twiml
    assert f'<Stream track="both_tracks" url="wss://{HOST}/media">' in twiml
    assert f'statusCallback="https://{HOST}/voice/status"' in twiml
    assert 'statusCallbackEvent="initiated ringing answered completed"' in twiml
    assert f">{SENIOR}</Number>" in twiml
    order = [twiml.index(tag) for tag in ("monitored by Palisade", "<Start>", "<Dial", "Goodbye.", "<Hangup")]
    assert order == sorted(order)  # disclosure, then stream, then dial, then the fall-through goodbye


def test_webhooks_reject_bad_or_missing_signatures(tmp_path):
    params = {"CallSid": "CAparent", "From": "+15555550199"}
    with TestClient(create_app(make_settings(tmp_path), stt_factory=None)) as client:
        assert client.post("/voice/incoming", data=params).status_code == 403
        assert signed_post(client, "/voice/incoming", params, token="wrong-token").status_code == 403
        assert signed_post(client, "/voice/status", params, token="wrong-token").status_code == 403
        assert signed_post(client, "/sms/status", params, token="wrong-token").status_code == 403
        assert signed_post(client, "/voice/incoming", params).status_code == 200


def test_signature_check_fails_closed_without_an_auth_token(tmp_path):
    settings = make_settings(tmp_path, TWILIO_AUTH_TOKEN="")
    with TestClient(create_app(settings, stt_factory=None)) as client:
        assert client.post("/voice/incoming", data={"CallSid": "CAparent"}).status_code == 403


def test_allowlisted_caller_is_connected_without_monitoring(tmp_path):
    settings = make_settings(tmp_path, ALLOWLIST="+1 (555) 555-0199, +15555550150")
    with TestClient(create_app(settings, stt_factory=None)) as client:
        twiml = incoming(client, caller="+15555550199").text
    assert "<Stream" not in twiml and "monitored" not in twiml
    assert f">{SENIOR}</Number>" in twiml


def test_missing_senior_number_hangs_up_politely(tmp_path):
    with TestClient(create_app(make_settings(tmp_path, SENIOR_PHONE=""), stt_factory=None)) as client:
        twiml = incoming(client).text
    assert "<Dial" not in twiml and "<Hangup" in twiml


def test_phone_numbers_are_masked_in_events_and_logs(tmp_path, caplog):
    app = create_app(make_settings(tmp_path), stt_factory=None)
    with caplog.at_level("INFO"), TestClient(app) as client:
        incoming(client, caller="+14045550142")
        (started,) = events_of(app, "call_started")
    assert started["caller"] == "***0142"
    assert "4045550142" not in caplog.text and "5555550101" not in caplog.text
    assert "4045550142" not in (tmp_path / "logs" / "events.jsonl").read_text()


def test_status_callback_records_the_seniors_leg(tmp_path):
    app = create_app(make_settings(tmp_path), stt_factory=None)
    with TestClient(app) as client:
        incoming(client)
        status = {"CallSid": "CAchild", "ParentCallSid": "CAparent", "CallStatus": "in-progress"}
        assert signed_post(client, "/voice/status", status).status_code == 204
        session = app.state.registry.get("CAparent")
        assert session.child_sid == "CAchild" and session.answered_at is not None
        assert events_of(app, "call_answered")


def test_events_token_protects_the_feed(tmp_path):
    with TestClient(create_app(make_settings(tmp_path, EVENTS_TOKEN="s3cret"), stt_factory=None)) as client:
        assert client.get("/metrics/last").status_code == 401
        assert client.get("/metrics/last?token=nope").status_code == 401
        assert client.get("/metrics/last?token=s3cret").status_code == 200
        assert client.get("/metrics/last", headers={"Authorization": "Bearer s3cret"}).status_code == 200
        assert client.get("/health").status_code == 200


# --- media stream ------------------------------------------------------------


def test_media_stream_without_stt_still_counts_frames(tmp_path, caplog):
    app = create_app(make_settings(tmp_path), stt_factory=None)
    with caplog.at_level("INFO"), TestClient(app) as client:
        token = stream_token(incoming(client).text)
        with client.websocket_connect("/media") as ws:
            ws.send_text(json.dumps({"event": "connected", "protocol": "Call", "version": "1.0.0"}))
            ws.send_text(start_message("CAparent", token))
            for _ in range(10):
                ws.send_text(frame("inbound"))
                ws.send_text(frame("outbound"))
            ws.send_text(json.dumps({"event": "stop"}))
        assert wait_for(lambda: events_of(app, "call_ended"))
    assert "media first frame call=CAparent track=inbound" in caplog.text
    assert "media first frame call=CAparent track=outbound" in caplog.text
    assert "{'inbound': 10, 'outbound': 10}" in caplog.text
    assert events_of(app, "stt_error")


def test_media_stream_with_wrong_token_is_rejected(tmp_path):
    app = create_app(make_settings(tmp_path), stt_factory=None)
    with TestClient(app) as client:
        incoming(client)
        with client.websocket_connect("/media") as ws:
            ws.send_text(start_message("CAparent", "not-the-token"))
            with pytest.raises(WebSocketDisconnect):
                ws.receive_text()
        with client.websocket_connect("/media") as ws:
            ws.send_text(start_message("CAunknown", "anything"))
            with pytest.raises(WebSocketDisconnect):
                ws.receive_text()
    assert not events_of(app, "stream_started")


def test_scam_call_is_transcribed_scored_and_ended(tmp_path, scripted_stt):
    app = create_app(make_settings(tmp_path), stt_factory=scripted_stt)
    with TestClient(app) as client:
        token = stream_token(incoming(client).text)
        signed_post(client, "/voice/status", {"CallSid": "CAchild", "ParentCallSid": "CAparent", "CallStatus": "in-progress"})
        with client.websocket_connect("/media") as ws:
            ws.send_text(start_message("CAparent", token))
            for _ in range(12):
                ws.send_text(frame("inbound"))
                ws.send_text(frame("outbound"))
                time.sleep(0.02)
            assert wait_for(lambda: any(r["kind"] == "END" for r in events_of(app, "action_result")))
            ws.send_text(json.dumps({"event": "stop"}))
        assert wait_for(lambda: events_of(app, "call_ended"))
        metrics = client.get("/metrics/last").json()

    transcripts = events_of(app, "transcript")
    assert transcripts == []  # transcript text is dropped from memory when the call ends
    scores = events_of(app, "score")
    assert scores and scores[-1]["risk"] >= 85 and "UNUSUAL_PAYMENT" in scores[-1]["flags"]
    actions = events_of(app, "action")  # usually WARN after the first line, then END after the second
    assert [action["kind"] for action in actions] in (["WARN", "END"], ["END"])
    assert actions[-1]["reason"].startswith("The caller asked for payment by gift card")
    result = events_of(app, "action_result")[-1]
    assert result["kind"] == "END" and result["call_control"] == "dry_run" and result["sms"] == "dry_run"
    (ended,) = events_of(app, "call_ended")
    assert ended["outcome"] == "ended_by_shield"
    assert all(stream.closed for stream in scripted_stt.created.values())

    assert metrics["last_end"]["kind"] == "END"
    assert 0 < metrics["seconds"] < 5
    assert set(metrics["last_action"]["stages_s"]) == {"speech_to_text", "scoring", "confirmation", "action"}

    persisted = (tmp_path / "logs" / "events.jsonl").read_text()
    assert '"type": "action"' in persisted and '"type": "score"' in persisted
    assert "gift cards" not in persisted and "warrant" not in persisted  # no transcript text on disk


def test_speakers_follow_the_track_mapping(tmp_path, scripted_stt):
    app = create_app(make_settings(tmp_path, GRACE_PERIOD_S="3600"), stt_factory=scripted_stt)
    seen = []
    original = app.state.bus.publish
    app.state.bus.publish = lambda kind, **data: (seen.append((kind, data)), original(kind, **data))[1]
    with TestClient(app) as client:
        token = stream_token(incoming(client).text)
        with client.websocket_connect("/media") as ws:
            ws.send_text(start_message("CAparent", token))
            for _ in range(6):
                ws.send_text(frame("inbound"))
                ws.send_text(frame("outbound"))
            ws.send_text(json.dumps({"event": "stop"}))
        assert wait_for(lambda: events_of(app, "call_ended"))
    spoken = {data["speaker"]: data["text"] for kind, data in seen if kind == "transcript"}
    assert spoken["CALLER"].startswith("This is Officer Daniels")
    assert spoken["SENIOR"].startswith("Oh my")
    flagged = {data["speaker"]: data.get("highlights") for kind, data in seen if kind == "transcript"}
    assert flagged["CALLER"] == ["This is Officer Daniels", "warrant", "arrest, stay on the line"]
    assert flagged["SENIOR"] is None  # only the caller's words are ever marked
    assert not [kind for kind, _ in seen if kind == "action"]  # still inside the grace period
