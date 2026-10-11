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


# --- a caregiver's mark: "Palisade got this wrong" -----------------------------


def mark_call(client, call_sid, wrong, token=""):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post(f"/calls/{call_sid}/mark", json={"wrong": wrong}, headers=headers)


def test_a_finished_call_can_be_marked_wrong_and_the_mark_taken_back(tmp_path, scripted_stt):  # noqa: F811
    app = create_app(make_settings(tmp_path), stt_factory=scripted_stt)
    with TestClient(app) as client:
        run_scam_call(client, app)
        assert client.get("/calls").json()["calls"][0]["marked_wrong"] is False

        answer = mark_call(client, "CAparent", True)
        assert answer.status_code == 200
        # The answer is the call as it now stands, the same summary /calls gives.
        assert answer.json() == client.get("/calls").json()["calls"][0]
        assert answer.json()["marked_wrong"] is True and answer.json()["outcome"] == "ended_by_shield"
        (said,) = events_of(app, "call_marked")
        assert said["call_sid"] == "CAparent" and said["wrong"] is True
        assert said["outcome"] == "ended_by_shield" and said["simulated"] is False

        assert mark_call(client, "CAparent", False).json()["marked_wrong"] is False
        assert client.get("/calls").json()["calls"][0]["marked_wrong"] is False
        assert [event["wrong"] for event in events_of(app, "call_marked")] == [True, False]


def test_marking_a_call_the_same_way_twice_says_it_once(tmp_path, scripted_stt):  # noqa: F811
    app = create_app(make_settings(tmp_path), stt_factory=scripted_stt)
    with TestClient(app) as client:
        run_scam_call(client, app)
        assert mark_call(client, "CAparent", False).json()["marked_wrong"] is False  # it was not marked to begin with
        assert events_of(app, "call_marked") == []
        mark_call(client, "CAparent", True)
        assert mark_call(client, "CAparent", True).json()["marked_wrong"] is True
        assert len(events_of(app, "call_marked")) == 1


def test_a_mark_changes_nothing_else_about_the_call(tmp_path, scripted_stt):  # noqa: F811
    app = create_app(make_settings(tmp_path), stt_factory=scripted_stt)
    with TestClient(app) as client:
        run_scam_call(client, app)
        before = client.get("/calls").json()["calls"][0]
        after = mark_call(client, "CAparent", True).json()
    assert {key: value for key, value in after.items() if key != "marked_wrong"} == {
        key: value for key, value in before.items() if key != "marked_wrong"
    }


def test_a_mark_survives_a_restart_and_keeps_none_of_the_call_s_words(tmp_path, scripted_stt):  # noqa: F811
    app = create_app(make_settings(tmp_path), stt_factory=scripted_stt)
    with TestClient(app) as client:
        run_scam_call(client, app)
        mark_call(client, "CAparent", True)
    with TestClient(create_app(make_settings(tmp_path), stt_factory=None)) as client:
        (call,) = client.get("/calls").json()["calls"]
        assert call["marked_wrong"] is True and call["outcome"] == "ended_by_shield"
        # Taken back after the restart, it stays taken back after the next one.
        mark_call(client, "CAparent", False)
    with TestClient(create_app(make_settings(tmp_path), stt_factory=None)) as client:
        assert client.get("/calls").json()["calls"][0]["marked_wrong"] is False

    lines = [json.loads(line) for line in (tmp_path / "logs" / "events.jsonl").read_text().splitlines()]
    marks = [line for line in lines if line["type"] == "call_marked"]
    assert [set(line) for line in marks] == [{"type", "ts", "call_sid", "wrong", "outcome", "simulated"}] * 2
    assert "gift cards" not in json.dumps(marks) and "warrant" not in json.dumps(marks)


def test_marking_a_call_needs_the_same_access_as_reading_the_calls(tmp_path):
    app = create_app(make_settings(tmp_path, EVENTS_TOKEN="s3cret"), stt_factory=None)
    with TestClient(app) as client:
        app.state.bus.publish("call_started", call_sid="CA1", caller="***0001", monitored=True, simulated=False)
        app.state.bus.publish("call_ended", call_sid="CA1", outcome="completed", duration_s=20)
        assert mark_call(client, "CA1", True).status_code == 401
        assert mark_call(client, "CA1", True, token="wrong").status_code == 401
        assert events_of(app, "call_marked") == [] and app.state.history.get("CA1")["marked_wrong"] is False
        assert mark_call(client, "CA1", True, token="s3cret").json()["marked_wrong"] is True


def test_only_a_finished_call_the_server_knows_can_be_marked(tmp_path):
    app = create_app(make_settings(tmp_path), stt_factory=None)
    with TestClient(app) as client:
        assert mark_call(client, "CAnobody", True).status_code == 404

        app.state.bus.publish("call_started", call_sid="CA1", caller="***0001", monitored=True, simulated=False)
        going = mark_call(client, "CA1", True)
        assert going.status_code == 409 and going.json() == {"detail": "This call is still going"}
        assert app.state.history.get("CA1")["marked_wrong"] is False

        app.state.bus.publish("call_ended", call_sid="CA1", outcome="completed", duration_s=20)
        # A call Palisade left alone can be marked too: that says it was a scam after all.
        assert mark_call(client, "CA1", True).json()["marked_wrong"] is True
        assert events_of(app, "call_marked")[-1]["outcome"] == "completed"


@pytest.mark.parametrize("body", [{}, {"wrong": "yes"}, {"wrong": 1}, {"wrong": None}, {"right": True}, []])
def test_a_mark_is_true_or_false_and_nothing_else(tmp_path, body):
    app = create_app(make_settings(tmp_path), stt_factory=None)
    with TestClient(app) as client:
        app.state.bus.publish("call_started", call_sid="CA1", caller="***0001", monitored=True, simulated=False)
        app.state.bus.publish("call_ended", call_sid="CA1", outcome="warned", duration_s=20)
        assert client.post("/calls/CA1/mark", json=body).status_code == 422
        assert app.state.history.get("CA1")["marked_wrong"] is False and events_of(app, "call_marked") == []


def test_a_demo_call_can_be_marked_and_the_mark_says_it_was_a_demo(tmp_path, fast_demo):
    app = create_app(make_settings(tmp_path, DEMO_MODE="true"), stt_factory=None)
    with TestClient(app) as client:
        call_sid = client.post("/demo/call?script=tiny&speed=3").json()["call_sid"]
        assert wait_for(lambda: events_of(app, "call_ended"), timeout=8)
        assert mark_call(client, call_sid, True).json()["marked_wrong"] is True
        (said,) = events_of(app, "call_marked")
        assert said["simulated"] is True


def test_history_takes_a_mark_only_for_a_call_it_has():
    history = CallHistory()
    history.record({"type": "call_marked", "ts": 5.0, "call_sid": "CAgone", "wrong": True})
    assert history.list() == [] and history.get("CAgone") is None
    history.record({"type": "call_started", "ts": 1.0, "call_sid": "CA1", "caller": "***0001"})
    assert history.get("CA1")["marked_wrong"] is False
    history.record({"type": "call_marked", "ts": 5.0, "call_sid": "CA1", "wrong": True})
    assert history.get("CA1")["marked_wrong"] is True


def test_a_log_from_before_marks_reads_as_no_mark(tmp_path):
    log = tmp_path / "events.jsonl"
    events = [
        {"type": "call_started", "ts": 1.0, "call_sid": "CA1", "caller": "***0001", "monitored": True},
        {"type": "call_ended", "ts": 9.0, "call_sid": "CA1", "outcome": "completed", "duration_s": 8},
    ]
    log.write_text("\n".join(json.dumps(event) for event in events) + "\n")
    (call,) = CallHistory.from_log(log).list()
    assert call["marked_wrong"] is False


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
