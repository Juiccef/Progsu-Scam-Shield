"""What a phone app reads: the recent-calls list and demo calls."""

import json
import time

import pytest
from starlette.testclient import TestClient

import demo
from history import CallHistory
from main import create_app
from tests.test_server import (  # noqa: F401  (scripted_stt is a fixture)
    events_of,
    frame,
    incoming,
    make_settings,
    scripted_stt,
    signed_post,
    start_message,
    stream_token,
    wait_for,
)

TINY_SCAM = {
    "id": "tiny_scam",
    "label": "scam",
    "category": "test",
    "notes": "Two caller lines, enough for a WARN and then an END.",
    "trigger_turn": 0,
    "turns": [
        {"t": 0, "speaker": "caller", "text": "There is a warrant for your arrest. Stay on the line. Do not tell your family."},
        {"t": 0.2, "speaker": "senior", "text": "Oh no."},
        {"t": 0.4, "speaker": "caller", "text": "Go and buy gift cards and read me the numbers on the back."},
    ],
}


@pytest.fixture
def fast_demo(monkeypatch):
    """Make a demo call take a fraction of a second instead of a minute."""
    monkeypatch.setattr(demo, "load_scripts", lambda path: [TINY_SCAM])
    monkeypatch.setattr(demo, "WORDS_PER_SECOND", 60.0)
    monkeypatch.setattr(demo, "INTERIM_EVERY_S", 0.05)
    monkeypatch.setattr(demo, "RECOGNITION_DELAY_S", 0.01)
    monkeypatch.setattr(demo, "FINAL_DELAY_S", 0.02)
    monkeypatch.setattr(demo, "PAUSE_BETWEEN_TURNS_S", 0.02)
    monkeypatch.setattr(demo, "LINGER_AFTER_LAST_TURN_S", 1.5)
    monkeypatch.setattr(demo, "LINGER_AFTER_END_S", 0.05)


def run_scam_call(client, app):
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


# --- recent calls ------------------------------------------------------------


def test_calls_is_empty_before_any_call(tmp_path):
    with TestClient(create_app(make_settings(tmp_path), stt_factory=None)) as client:
        assert client.get("/calls").json() == {"calls": []}


def test_a_finished_call_is_summarized_without_its_transcript(tmp_path, scripted_stt):  # noqa: F811
    app = create_app(make_settings(tmp_path), stt_factory=scripted_stt)
    with TestClient(app) as client:
        run_scam_call(client, app)
        body = client.get("/calls").json()

    (call,) = body["calls"]
    assert call["call_sid"] == "CAparent" and call["caller"] == "***0199"
    assert call["outcome"] == "ended_by_shield" and call["simulated"] is False
    assert call["peak_risk"] >= 85 and "UNUSUAL_PAYMENT" in call["flags"]
    assert call["answered_at"] and call["ended_at"] and call["duration_s"] is not None
    assert call["actions"][-1]["kind"] == "END"
    assert call["actions"][-1]["reason"].startswith("The caller asked for payment by gift card")
    assert 0 < call["actions"][-1]["seconds"] < 5
    assert call["timeline"] and all(len(point) == 2 for point in call["timeline"])
    assert "gift cards" not in json.dumps(body) and "warrant" not in json.dumps(body)


def test_history_survives_a_restart(tmp_path, scripted_stt):  # noqa: F811
    app = create_app(make_settings(tmp_path), stt_factory=scripted_stt)
    with TestClient(app) as client:
        run_scam_call(client, app)
    with TestClient(create_app(make_settings(tmp_path), stt_factory=None)) as client:
        (call,) = client.get("/calls").json()["calls"]
    assert call["outcome"] == "ended_by_shield" and call["peak_risk"] >= 85


def test_calls_needs_the_events_token(tmp_path):
    with TestClient(create_app(make_settings(tmp_path, EVENTS_TOKEN="s3cret"), stt_factory=None)) as client:
        assert client.get("/calls").status_code == 401
        assert client.get("/calls?token=s3cret").status_code == 200
        assert client.get("/demo/scripts").status_code == 401


def test_history_keeps_the_newest_calls_and_lists_them_first():
    history = CallHistory(limit=2)
    for number in range(3):
        history.record({"type": "call_started", "ts": 100 + number, "call_sid": f"CA{number}", "caller": "***0001"})
    assert [call["call_sid"] for call in history.list()] == ["CA2", "CA1"]


def test_history_settles_calls_cut_off_by_a_crash(tmp_path):
    log = tmp_path / "events.jsonl"
    events = [
        {"type": "call_started", "ts": 1.0, "call_sid": "CA1", "caller": "***0001", "monitored": True},
        {"type": "score", "ts": 2.0, "call_sid": "CA1", "t": 12.0, "risk": 91, "flags": ["THREAT"]},
        {"type": "action", "ts": 2.1, "call_sid": "CA1", "kind": "END", "risk": 91, "reason": "x"},
    ]
    log.write_text("\n".join(json.dumps(event) for event in events) + "\nnot json at all\n")
    (call,) = CallHistory.from_log(log).list()
    assert call["outcome"] == "ended_by_shield" and call["ended_at"] == 2.1 and call["flags"] == ["THREAT"]
    assert CallHistory.from_log(tmp_path / "missing.jsonl").list() == []


# --- demo calls --------------------------------------------------------------


def test_demo_is_off_by_default(tmp_path):
    with TestClient(create_app(make_settings(tmp_path), stt_factory=None)) as client:
        assert client.get("/demo/scripts").json() == {"enabled": False, "scripts": []}
        assert client.post("/demo/call?script=scam_01").status_code == 404
        assert client.get("/health").json()["demo_mode"] is False


def test_demo_scripts_are_listed_when_demo_mode_is_on(tmp_path):
    with TestClient(create_app(make_settings(tmp_path, DEMO_MODE="true"), stt_factory=None)) as client:
        body = client.get("/demo/scripts").json()
    assert body["enabled"] is True and len(body["scripts"]) == 16
    first = body["scripts"][0]
    assert first["id"] == "scam_01_benefits_suspension" and first["label"] == "scam"
    assert first["turns"] == 11 and 50 < first["duration_s"] < 80


def test_demo_call_runs_the_real_pipeline_but_never_touches_twilio(tmp_path, fast_demo):
    twilio_calls = []
    settings = make_settings(tmp_path, DEMO_MODE="true", DRY_RUN_ACTIONS="false")  # live actions for real calls
    app = create_app(settings, stt_factory=None, twilio_client_factory=lambda: twilio_calls.append("client created"))
    with TestClient(app) as client:
        started = client.post("/demo/call?script=tiny&speed=3")
        assert started.status_code == 202 and started.json()["script"] == "tiny_scam"
        call_sid = started.json()["call_sid"]
        assert client.post("/demo/call?script=tiny").status_code == 409  # one demo at a time
        assert client.post("/demo/call?script=nope").status_code in (404, 409)
        assert wait_for(lambda: events_of(app, "call_ended"), timeout=8)
        metrics = client.get("/metrics/last").json()
        (call,) = client.get("/calls").json()["calls"]

    (begun,) = events_of(app, "call_started")
    assert begun["simulated"] is True and begun["call_sid"] == call_sid and call_sid.startswith("CAdemo")
    kinds = [action["kind"] for action in events_of(app, "action")]
    assert kinds[-1] == "END"
    result = events_of(app, "action_result")[-1]
    assert result["call_control"] == "dry_run" and result["sms"] == "dry_run"
    assert twilio_calls == []  # no Twilio client was ever built for the demo call
    assert metrics["seconds"] is None  # a demo never replaces the latency of the last real action
    assert call["simulated"] is True and call["outcome"] == "ended_by_shield"
    assert (tmp_path / "logs" / "events.jsonl").read_text().count("gift cards") == 0


def test_demo_speech_arrives_as_growing_partial_text_then_a_final_line(fast_demo, monkeypatch):
    monkeypatch.setattr(demo, "WORDS_PER_SECOND", 20.0)
    heard = []

    class Listener:
        call_sid = "CAdemo"

        class scorer:
            class engine:
                ended = False

        def add_transcript(self, speaker, text, *, final, start_at, end_at):
            heard.append((speaker, text, final))
            assert start_at <= end_at <= time.time() + 0.01

    import asyncio

    asyncio.run(demo.play(Listener(), {"turns": TINY_SCAM["turns"][:1]}, speed=1.0))
    finals = [entry for entry in heard if entry[2]]
    partials = [entry for entry in heard if not entry[2]]
    assert finals == [("CALLER", TINY_SCAM["turns"][0]["text"], True)]
    assert partials and all(TINY_SCAM["turns"][0]["text"].startswith(text) for _, text, _ in partials)
    assert [len(text) for _, text, _ in partials] == sorted(len(text) for _, text, _ in partials)
