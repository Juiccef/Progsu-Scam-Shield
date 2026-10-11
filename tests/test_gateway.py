import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from fastapi.testclient import TestClient
from twilio.base.exceptions import TwilioRestException

from events import EventBus
from gateway import GatewayMessage, GatewayStore, MAX_SMS_LENGTH, SendResult, TTL, forwarded_sms
from gateway_sms import SMSDelivery
from main import create_app
from tests.test_server import make_settings, signed_post, wait_for

BRIDGE = {"Authorization": "Bearer bridge-private"}
CAREGIVER = {"Authorization": "Bearer caregiver-private"}
LINE = "+15555550100"
SENDER = "+15555550999"
SID = "SM" + "a" * 32
MID = "twilio_" + SID
SEND_SID = "SM" + "b" * 32
SCAM = "USPS: pay the redelivery fee immediately at https://parcel-fee.example/pay"


def settings(tmp_path, **changes):
    env = dict(TEXT_GATEWAY_ENABLED="true", TEXT_FORWARDING_ENABLED="true",
               PHOTON_BRIDGE_TOKEN="bridge-private", EVENTS_TOKEN="caregiver-private",
               GATEWAY_ENCRYPTION_KEY=Fernet.generate_key().decode())
    env.update(changes)
    return make_settings(tmp_path, **env)


def incoming(text=SCAM, message_id=MID, **changes):
    return GatewayMessage(message_id=message_id, sender=SENDER, line=LINE, text=text, **changes)


def form(text=SCAM, **changes):
    return {"MessageSid": SID, "From": SENDER, "To": LINE, "Body": text, "NumMedia": "0", **changes}


def summary(client, message_id=MID):
    messages = client.get("/gateway/messages", headers=CAREGIVER).json()["messages"]
    return next((m for m in messages if m["message_id"] == message_id), None)


def submit(client, text=SCAM, **changes):
    response = signed_post(client, "/sms/incoming", form(text, **changes))
    assert response.status_code == 200 and response.text == "<Response></Response>"
    message_id = "twilio_" + changes.get("MessageSid", SID)
    assert wait_for(lambda: (m := summary(client, message_id)) and m["status"] != "pending")
    return summary(client, message_id)


def claim(client):
    response = client.post("/integrations/photon/outbox/claim", headers=BRIDGE)
    assert response.status_code == 200
    return response.json()["job"]


def relay(client, app):
    return client.portal.call(app.state.gateway.store.claim, "relay")


def decide(client, action, message_id=MID):
    return client.post(f"/gateway/messages/{message_id}/decision", json={"action": action}, headers=CAREGIVER)


def report(client, job, outcome="sent"):
    return client.post(f"/integrations/photon/outbox/{job['job_id']}/result",
                       json={"attempt_id": job["attempt_id"], "outcome": outcome}, headers=BRIDGE)


@pytest.mark.parametrize("score,status", [(29, "queued"), (30, "held"), (70, "held")])
def test_twilio_screening_boundary_and_separate_transport_queues(score, status, tmp_path):
    app = create_app(settings(tmp_path), stt_factory=None)
    async def evaluate(text):
        return {"risk": score, "label": "safe" if score < 30 else "suspicious", "flags": []}
    app.state.gateway.scorer.evaluate = evaluate
    with TestClient(app) as client:
        assert submit(client)["status"] == status
        if status == "queued":
            assert claim(client) is None  # Spectrum can never take a senior SMS relay
            assert relay(client, app)["kind"] == "relay"
        else:
            assert relay(client, app) is None
            assert claim(client)["kind"] == "caregiver_alert"


def test_gateway_auth_signature_and_validation(tmp_path):
    with TestClient(create_app(settings(tmp_path), stt_factory=None)) as client:
        assert client.post("/sms/incoming", data=form()).status_code == 403
        assert signed_post(client, "/sms/incoming", form(), token="wrong").status_code == 403
        assert client.get("/gateway/messages").status_code == 401
        assert client.get("/gateway/messages", headers=BRIDGE).status_code == 401
        assert client.post("/integrations/photon/outbox/claim").status_code == 401
        assert client.post("/integrations/photon/outbox/claim", headers=CAREGIVER).status_code == 401
        assert client.post("/integrations/photon/messages", json={}).status_code == 404
        for change in [{"Body": " "}, {"Body": "x" * 10001}, {"MessageSid": "../bad"},
                       {"From": "a\nb"}, {"To": "+15555550901"}, {"NumMedia": "bad"}]:
            assert signed_post(client, "/sms/incoming", form(**change)).status_code == 400
        assert client.get("/gateway/messages/absent", headers=CAREGIVER).status_code == 404


@pytest.mark.parametrize("change,match", [
    ({"SENIOR_PHONE": ""}, "requires"), ({"EVENTS_TOKEN": ""}, "requires"),
    ({"PHOTON_BRIDGE_TOKEN": ""}, "requires"), ({"GATEWAY_ENCRYPTION_KEY": "bad"}, "Fernet"),
    ({"TWILIO_VALIDATE_SIGNATURE": "false"}, "signature"), ({"SENIOR_PHONE": LINE}, "distinct"),
])
def test_gateway_configuration_fails_closed(tmp_path, change, match):
    """A wrong setting never runs the gateway half set up. The server itself still starts: calls come first."""
    with pytest.raises(ValueError, match=match):
        GatewayStore(settings(tmp_path / "store", **change), EventBus())
    conf = settings(tmp_path / "server", **change)
    app = create_app(conf, stt_factory=None)
    assert app.state.gateway is None and not (conf.log_dir / "gateway").exists()
    with TestClient(app) as client:
        health = client.get("/health").json()
        assert health["ok"] and health["gateway"]["enabled"] is False
        assert match in health["gateway"]["error"]
        assert client.get("/gateway/messages", headers=CAREGIVER).status_code == 503


def test_hold_release_and_fixed_footer_with_private_storage(tmp_path):
    conf = settings(tmp_path)
    app = create_app(conf, stt_factory=None)
    with TestClient(app) as client:
        assert submit(client)["status"] == "held"
        assert submit(client)["status"] == "held"
        assert len(client.get("/gateway/messages", headers=CAREGIVER).json()["messages"]) == 1
        detail_response = client.get(f"/gateway/messages/{MID}", headers=CAREGIVER)
        assert detail_response.headers["Cache-Control"] == "no-store"
        detail = detail_response.json()
        assert detail["sender"] == "***0999" and detail["source"] == "twilio"
        assert detail["text"] == SCAM and detail["original_sender"] == SENDER
        alert = claim(client)
        assert alert["recipient"] == conf.photon_caregiver_address and alert["line"] == ""
        assert SCAM not in alert["text"] and "parcel-fee.example" not in alert["text"]
        assert report(client, alert).status_code == 200
        why = client.get("/integrations/photon/status", headers=BRIDGE).json()
        assert why["shield_number"] == LINE and "fee" in why["why"] and SCAM not in why["why"]
        assert decide(client, "release").json()["status"] == "queued"
        assert decide(client, "release").status_code == 200
        job = relay(client, app)
        assert job["recipient"] == conf.senior_phone and job["line"] == LINE
        assert job["text"] == forwarded_sms(SCAM, SENDER, released=True)
        assert "Released after caregiver review." in job["text"]
        assert "low scam risk" not in job["text"]
        assert relay(client, app) is None
        assert report(client, job).status_code == 403  # Photon token cannot complete a Twilio send
        complete = lambda: app.state.gateway.store.complete(job["job_id"], SendResult(
            attempt_id=job["attempt_id"], outcome="sent"), kind="relay", provider_sid=SEND_SID)
        assert client.portal.call(complete)["status"] == "sent"
        assert "text" not in client.get(f"/gateway/messages/{MID}", headers=CAREGIVER).json()
        assert decide(client, "discard").status_code == 409
        assert client.get("/calls", headers=CAREGIVER).json() == {"calls": []}
        events = json.dumps(app.state.bus.recent())
        assert not any(v in events for v in [SCAM, "parcel-fee.example", SENDER])
    persisted = (conf.log_dir / "events.jsonl").read_text()
    db_bytes = (conf.log_dir / "gateway/queue.sqlite3").read_bytes()
    for value in [SCAM, SENDER, "parcel-fee.example"]:
        assert value not in persisted and value.encode() not in db_bytes
    assert (conf.log_dir / "gateway/queue.sqlite3").stat().st_mode & 0o777 == 0o600


def test_allowed_text_stays_queued_during_staging_and_survives_restart(tmp_path):
    conf = settings(tmp_path, TEXT_FORWARDING_ENABLED="false")
    app = create_app(conf, stt_factory=None)
    with TestClient(app) as client:
        assert submit(client, "Your appointment is tomorrow at 2pm.")["status"] == "queued"
        assert relay(client, app) is None and claim(client) is None
        assert client.get("/gateway/messages", headers=CAREGIVER).json()["shield_number"] == LINE
    app = create_app(replace(conf, text_forwarding_enabled=True), stt_factory=None)
    with TestClient(app) as client:
        job = relay(client, app)
        # An ordinary text goes through as it is: no note, no sender, nothing added.
        assert "screening" not in job["text"] and "Original sender" not in job["text"]
        assert job["text"] == job["text"].strip()


@pytest.mark.parametrize("failure", ["timeout", "exception", "missing_model"])
def test_incomplete_analysis_never_forwards(tmp_path, failure):
    conf = settings(tmp_path)
    if failure == "missing_model":
        conf = replace(conf, scorer_model="configured-model")
    app = create_app(conf, stt_factory=None, llm=None, message_llm=None)
    async def evaluate(text):
        if failure == "exception":
            raise RuntimeError("private content")
        return {"risk": 0, "label": "safe", "flags": [], "llm_error": "timeout"}
    app.state.gateway.scorer.evaluate = evaluate
    with TestClient(app) as client:
        scored = submit(client, "Ordinary message")
        assert scored["status"] == "held" and scored["hold_reason"] == "analysis_incomplete"
        assert relay(client, app) is None
        assert claim(client)["kind"] == "caregiver_alert"


@pytest.mark.parametrize("caption", ["", "Your appointment is tomorrow."])
def test_media_is_held_without_forwarding_even_with_benign_caption(tmp_path, caption):
    app = create_app(settings(tmp_path), stt_factory=None)
    with TestClient(app) as client:
        result = submit(client, caption, NumMedia="1", MediaUrl0="https://private.example/attachment")
        assert result["hold_reason"] == "unsupported_content" and not result["review_eligible"]
        assert decide(client, "release").status_code == 409
        assert relay(client, app) is None
        assert decide(client, "discard").json()["status"] == "discarded"
        assert decide(client, "discard").status_code == 200 and claim(client) is None


def test_a_text_that_passed_goes_as_it_is_and_a_released_one_must_fit_with_its_note(tmp_path):
    from scorer.messages import MessageScorer
    from gateway import GatewayService
    service = GatewayService(settings(tmp_path), EventBus(), MessageScorer())
    # A text that passed carries nothing added, so it may be as long as the limit allows.
    assert asyncio.run(service.ingest(incoming("x" * MAX_SMS_LENGTH)))["status"] == "queued"
    result = asyncio.run(service.ingest(incoming("x" * (MAX_SMS_LENGTH + 1), message_id="too_long")))
    assert result["hold_reason"] == "message_too_long" and not result["review_eligible"]
    with pytest.raises(HTTPException):
        service.store.decide("too_long", "release")
    assert service.store.claim("relay")["text"] == "x" * MAX_SMS_LENGTH
    # A scam may be released, and then gets the note: one that would not fit with it can only be discarded.
    room = MAX_SMS_LENGTH - len(forwarded_sms("", SENDER, released=True))
    padding = "x" * (room - len(SCAM) - 1)
    fits = asyncio.run(service.ingest(incoming(SCAM + " " + padding, message_id="fits")))
    assert fits["hold_reason"] == "scam_signals" and fits["review_eligible"]
    over = asyncio.run(service.ingest(incoming(SCAM + " " + padding + "xx", message_id="over")))
    assert over["hold_reason"] == "message_too_long" and not over["review_eligible"]
    service.store.close()


def test_expiry_release_race_restart_and_deduplication(tmp_path):
    conf = settings(tmp_path)
    with TestClient(create_app(conf, stt_factory=None)) as client:
        submit(client)
    app = create_app(conf, stt_factory=None)
    with TestClient(app) as client:
        original = submit(client)
        app.state.gateway.store.clock = lambda: original["ts"] + TTL
        assert decide(client, "release").status_code == 409
        detail = client.get(f"/gateway/messages/{MID}", headers=CAREGIVER).json()
        assert detail["status"] == "expired" and "text" not in detail
        assert relay(client, app) is None and claim(client) is None


@pytest.mark.parametrize("operation", ["release", "claim"])
def test_expiry_is_rechecked_inside_write_transaction(tmp_path, operation):
    store = GatewayStore(settings(tmp_path), EventBus())
    row = store.ingest(incoming())
    store.finish_screening(MID, {"risk": 30 if operation == "release" else 0, "label": "suspicious", "flags": []})
    deadline = row["expires_at"]
    times = iter([deadline - 0.001, deadline])
    store.clock = lambda: next(times, deadline)
    if operation == "release":
        with pytest.raises(HTTPException) as error:
            store.decide(MID, "release")
        assert error.value.status_code == 409
    else:
        assert store.claim("relay") is None
    store.cleanup()
    assert store.detail(MID)["status"] == "expired"
    store.close()


def test_crash_during_send_is_uncertain_and_not_retried(tmp_path):
    conf = settings(tmp_path)
    store = GatewayStore(conf, EventBus())
    store.ingest(incoming("Your appointment is tomorrow."))
    store.finish_screening(MID, {"risk": 0, "label": "safe", "flags": []})
    job = store.claim("relay")
    store.close()
    store = GatewayStore(conf, EventBus())
    assert store.detail(MID)["status"] == "delivery_unknown" and store.claim("relay") is None
    store.complete(job["job_id"], SendResult(attempt_id=job["attempt_id"], outcome="sent"), kind="relay")
    assert store.detail(MID)["status"] == "sent" and store.claim("relay") is None
    store.close()


def test_concurrent_release_and_claim_are_atomic(tmp_path):
    conf = settings(tmp_path)
    first, second = GatewayStore(conf, EventBus()), GatewayStore(conf, EventBus())
    first.ingest(incoming())
    first.finish_screening(MID, {"risk": 30, "label": "suspicious", "flags": []})
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda s: s.decide(MID, "release"), [first, second]))
        assert all(r["status"] == "queued" for r in results)
        jobs = list(pool.map(lambda s: s.claim("relay"), [first, second]))
        assert sum(job is not None for job in jobs) == 1
    first.close()
    second.close()


def test_pending_accept_is_durable_and_does_not_wait_for_analysis(tmp_path):
    conf = settings(tmp_path)
    app = create_app(conf, stt_factory=None)
    started = asyncio.Event()
    async def slow(text):
        started.set()
        await asyncio.sleep(30)
    app.state.gateway.scorer.evaluate = slow
    with TestClient(app) as client:
        before = time.monotonic()
        assert signed_post(client, "/sms/incoming", form()).status_code == 200
        assert time.monotonic() - before < 2
        assert wait_for(started.is_set)
        assert summary(client)["status"] == "pending"
    with TestClient(create_app(conf, stt_factory=None)) as client:
        assert wait_for(lambda: summary(client)["status"] == "held")


def test_participant_replies_and_echoes_never_create_a_relay(tmp_path):
    with TestClient(create_app(settings(tmp_path), stt_factory=None)) as client:
        for sender in [LINE, "+15555550101", "+15555550102"]:
            assert signed_post(client, "/sms/incoming", form(From=sender)).status_code == 200
        assert client.get("/gateway/messages", headers=CAREGIVER).json()["messages"] == []


def test_background_pipeline_forwards_only_approved_and_released_texts(tmp_path):
    sends = []
    def send(**params):
        sends.append(params)
        return SimpleNamespace(sid="SM" + str(len(sends)) * 32)
    app = create_app(settings(tmp_path, DRY_RUN_ACTIONS="false"), stt_factory=None,
                     twilio_client_factory=lambda: SimpleNamespace(messages=SimpleNamespace(create=send)))
    with TestClient(app) as client:
        ordinary = "Your appointment is tomorrow at 2pm."
        submit(client, ordinary)
        assert wait_for(lambda: summary(client)["status"] == "sent")
        assert len(sends) == 1 and sends[0]["body"] == forwarded_sms(ordinary, SENDER)
        scam_sid = "SM" + "c" * 32
        scam_id = "twilio_" + scam_sid
        assert submit(client, SCAM, MessageSid=scam_sid)["status"] == "held"
        assert len(sends) == 1  # no scam delivery before caregiver release
        assert decide(client, "release", scam_id).status_code == 200
        assert decide(client, "release", scam_id).status_code == 200
        assert wait_for(lambda: summary(client, scam_id)["status"] == "sent")
        submit(client, SCAM, MessageSid=scam_sid)  # duplicate webhook after delivery
        assert len(sends) == 2 and sends[1]["body"] == forwarded_sms(SCAM, SENDER, released=True)
        assert all(s["from_"] == LINE and s["to"] == "+15555550101" for s in sends)


def test_accept_during_scoring_triggers_an_immediate_second_scan(tmp_path):
    app = create_app(settings(tmp_path), stt_factory=None)
    started = asyncio.Event()
    async def evaluate(text):
        if text == "First appointment":
            started.set()
            await asyncio.sleep(0.1)
        return {"risk": 0, "label": "safe", "flags": []}
    app.state.gateway.scorer.evaluate = evaluate
    with TestClient(app) as client:
        assert signed_post(client, "/sms/incoming", form("First appointment")).status_code == 200
        assert wait_for(started.is_set)
        next_sid = "SM" + "c" * 32
        assert signed_post(client, "/sms/incoming", form("Second appointment", MessageSid=next_sid)).status_code == 200
        assert wait_for(lambda: summary(client, "twilio_" + next_sid)["status"] == "queued", timeout=2)


@pytest.mark.parametrize("failure,status", [(None, "sent"), ("ambiguous", "delivery_unknown"),
                                           ("rejected", "delivery_failed"), ("missing_ack", "delivery_unknown")])
def test_twilio_delivery_sender_footer_outcome_and_no_retry(tmp_path, caplog, failure, status):
    conf = settings(tmp_path, DRY_RUN_ACTIONS="false", TWILIO_SMS_FROM="+15555550777")
    store = GatewayStore(conf, EventBus())
    original = "Your appointment is tomorrow."
    store.ingest(incoming(original))
    store.finish_screening(MID, {"risk": 0, "label": "safe", "flags": []})
    sends = []
    def send(**params):
        sends.append(params)
        if failure == "ambiguous":
            raise RuntimeError(SCAM)
        if failure == "rejected":
            raise TwilioRestException(400, "/test", msg=SCAM, code=21610)
        return SimpleNamespace(sid=None if failure == "missing_ack" else SEND_SID)
    worker = SMSDelivery(store, client_factory=lambda: SimpleNamespace(messages=SimpleNamespace(create=send)),
                         public_host=lambda: "shield.example.test")
    assert asyncio.run(worker.drain())
    assert not asyncio.run(worker.drain())
    assert len(sends) == 1
    assert sends[0]["to"] == conf.senior_phone and sends[0]["from_"] == LINE
    assert sends[0]["body"] == forwarded_sms(original, SENDER)
    assert "/gateway/sms/status/" in sends[0]["status_callback"]
    assert store.detail(MID)["status"] == status
    assert SCAM not in caplog.text and SENDER not in caplog.text
    store.close()


def test_dry_run_never_claims_or_sends_sms(tmp_path):
    store = GatewayStore(settings(tmp_path), EventBus())
    store.ingest(incoming("Appointment tomorrow."))
    store.finish_screening(MID, {"risk": 0, "label": "safe", "flags": []})
    worker = SMSDelivery(store, client_factory=lambda: pytest.fail("No dry-run Twilio client"))
    assert not asyncio.run(worker.drain()) and store.detail(MID)["status"] == "queued"
    store.close()


def test_send_timeout_is_uncertain_even_if_twilio_later_accepts(tmp_path):
    store = GatewayStore(settings(tmp_path, DRY_RUN_ACTIONS="false"), EventBus())
    store.ingest(incoming("Appointment tomorrow."))
    store.finish_screening(MID, {"risk": 0, "label": "safe", "flags": []})
    sends = []
    def send(**params):
        sends.append(params)
        time.sleep(0.04)
        return SimpleNamespace(sid=SEND_SID)
    worker = SMSDelivery(store, client_factory=lambda: SimpleNamespace(messages=SimpleNamespace(create=send)), timeout_s=0.001)
    assert asyncio.run(worker.drain())
    assert store.detail(MID)["status"] == "delivery_unknown"
    assert not asyncio.run(worker.drain()) and len(sends) == 1
    store.close()


def test_signed_delivery_callbacks_resolve_uncertain_sends_and_ignore_stale_status(tmp_path):
    app = create_app(settings(tmp_path), stt_factory=None)
    with TestClient(app) as client:
        submit(client, "Appointment tomorrow.")
        job = relay(client, app)
        callback = f"/gateway/sms/status/{job['job_id']}/{job['attempt_id']}"
        params = {"MessageSid": SEND_SID, "MessageStatus": "delivered", "From": LINE, "To": "+15555550101"}
        assert client.post(callback, data=params).status_code == 403
        assert signed_post(client, callback, {**params, "To": SENDER}).status_code == 400
        assert signed_post(client, callback, params).status_code == 204
        assert summary(client)["status"] == "delivered"
        assert signed_post(client, callback, {**params, "MessageStatus": "queued"}).status_code == 204
        assert summary(client)["status"] == "delivered"
        assert signed_post(client, callback, {**params, "MessageSid": "SM" + "c" * 32}).status_code == 409
        assert "text" not in client.get(f"/gateway/messages/{MID}", headers=CAREGIVER).json()
        complete = lambda: app.state.gateway.store.complete(job["job_id"], SendResult(
            attempt_id=job["attempt_id"], outcome="delivery_unknown"), kind="relay")
        assert client.portal.call(complete)["status"] == "delivered"  # callback won the create-result race
        assert relay(client, app) is None


def test_carrier_rejection_is_visible_and_not_retried(tmp_path):
    app = create_app(settings(tmp_path), stt_factory=None)
    with TestClient(app) as client:
        submit(client, "Appointment tomorrow.")
        job = relay(client, app)
        callback = f"/gateway/sms/status/{job['job_id']}/{job['attempt_id']}"
        params = {"MessageSid": SEND_SID, "MessageStatus": "undelivered", "From": LINE, "To": "+15555550101"}
        assert signed_post(client, callback, params).status_code == 204
        assert summary(client)["status"] == "delivery_failed" and relay(client, app) is None


def test_old_photon_records_survive_upgrade_but_cannot_be_released_as_sms(tmp_path):
    conf = settings(tmp_path)
    store = GatewayStore(conf, EventBus())
    store.ingest(incoming())
    store.db.execute("UPDATE messages SET source='photon'")
    store.db.commit()
    store.close()
    store = GatewayStore(conf, EventBus())
    message = store.detail(MID)
    assert message["status"] == "held" and message["text"] == SCAM and not message["review_eligible"]
    assert message["hold_reason"] == "transport_changed" and store.claim("relay") is None
    with pytest.raises(HTTPException):
        store.decide(MID, "release")
    store.close()


def test_delayed_screening_events_use_update_time_for_foreground_alerts(tmp_path):
    bus = EventBus()
    store = GatewayStore(settings(tmp_path), bus, clock=lambda: 1000)
    store.ingest(incoming())
    result = store.finish_screening(MID, {"risk": 70, "label": "likely_scam", "flags": []})
    event = bus.recent()[-1]
    assert event["ts"] > result["ts"] and event["received_at"] == result["ts"] == 1000
    store.close()
