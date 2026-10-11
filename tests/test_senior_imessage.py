"""Forwarding an approved text to the senior through the iMessage agent (TEXT_FORWARD_VIA=imessage).

Two transports can carry a text to the senior, and the server decides which: SMS from the Twilio number,
or iMessage through the Photon agent that already alerts the caregiver. Each may take only its own jobs.
"""

import json

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from gateway import GatewayStore, SendResult, forwarded_sms
from events import EventBus
from main import create_app
from tests.test_gateway import BRIDGE, CAREGIVER, LINE, MID, SCAM, SENDER, decide, relay, report, settings, submit

ORDINARY = "Your appointment is tomorrow at 2pm. Reply C to confirm."
ORDINARY_SID = "SM" + "c" * 32
ORDINARY_ID = "twilio_" + ORDINARY_SID
SENIOR = "senior@example.com"


def imessage(tmp_path, **changes):
    """Settings for a server that forwards by iMessage, live (not practice mode), with forwarding on."""
    return settings(tmp_path, **{"TEXT_FORWARD_VIA": "imessage", "SENIOR_IMESSAGE_ADDRESS": SENIOR,
                                 "DRY_RUN_ACTIONS": "false", **changes})


def take(client, kind="senior_imessage"):
    response = client.post("/integrations/photon/outbox/claim", json={"kind": kind}, headers=BRIDGE)
    assert response.status_code == 200, response.text
    return response.json()["job"]


def finish(client, job, outcome="sent"):
    return client.post(f"/integrations/photon/outbox/{job['job_id']}/result",
                       json={"attempt_id": job["attempt_id"], "outcome": outcome}, headers=BRIDGE)


def detail(client, message_id):
    return client.get(f"/gateway/messages/{message_id}", headers=CAREGIVER).json()


# --- who may take what ---------------------------------------------------------


def test_by_default_the_agent_has_no_text_for_the_senior_to_take(tmp_path):
    """With SMS as the transport, a text for the senior is the SMS worker's and the agent gets nothing."""
    app = create_app(settings(tmp_path), stt_factory=None)
    with TestClient(app) as client:
        assert submit(client, ORDINARY, MessageSid=ORDINARY_SID)["status"] == "queued"
        assert take(client) is None
        assert relay(client, app)["kind"] == "relay"


def test_the_agent_can_ask_only_for_its_two_kinds_of_job(tmp_path):
    with TestClient(create_app(imessage(tmp_path), stt_factory=None)) as client:
        for kind in ("relay", "anything", ""):
            response = client.post("/integrations/photon/outbox/claim", json={"kind": kind}, headers=BRIDGE)
            assert response.status_code == 422
        # No body at all is still a request for a caregiver alert, as the agent has always made it.
        assert client.post("/integrations/photon/outbox/claim", headers=BRIDGE).json() == {"job": None}


def test_an_approved_text_goes_to_the_agent_and_not_to_the_sms_worker(tmp_path):
    app = create_app(imessage(tmp_path), stt_factory=None)
    with TestClient(app) as client:
        assert submit(client, ORDINARY, MessageSid=ORDINARY_SID)["status"] == "queued"
        assert relay(client, app) is None  # the SMS worker is never handed it
        assert client.portal.call(app.state.gateway.store.claim, "senior_imessage") is not None  # the store hands it over
        # (that claim was the agent's turn; a second is refused, the same text is never handed out twice)
        assert take(client) is None


def test_the_job_names_the_senior_and_carries_the_fixed_note(tmp_path):
    with TestClient(create_app(imessage(tmp_path), stt_factory=None)) as client:
        submit(client, ORDINARY, MessageSid=ORDINARY_SID)
        job = take(client)
        assert job["kind"] == "senior_imessage" and job["message_id"] == ORDINARY_ID
        assert job["recipient"] == SENIOR and job["line"] == ""  # a shared Photon line, as for the caregiver
        assert job["text"] == forwarded_sms(ORDINARY, SENDER, released=False)
        assert "Passed automated screening" in job["text"]
        assert take(client) is None  # claimed once
        # While it is being sent the app is told, and the text is still kept in case the send is not confirmed.
        assert detail(client, ORDINARY_ID)["status"] == "sending"


def test_a_report_from_the_agent_ends_the_text_and_deletes_it(tmp_path):
    with TestClient(create_app(imessage(tmp_path), stt_factory=None)) as client:
        submit(client, ORDINARY, MessageSid=ORDINARY_SID)
        job = take(client)
        assert finish(client, job).json()["status"] == "sent"
        after = detail(client, ORDINARY_ID)
        assert after["status"] == "sent" and "text" not in after  # the words are gone once it is sent
        assert finish(client, job).status_code == 200  # a repeated report changes nothing
        assert finish(client, job, "delivery_unknown").json()["status"] == "sent"  # and cannot make it unsure again


@pytest.mark.parametrize("outcome", ["delivery_unknown", "delivery_failed"])
def test_a_send_that_failed_or_is_not_known_is_never_repeated(tmp_path, outcome):
    with TestClient(create_app(imessage(tmp_path), stt_factory=None)) as client:
        submit(client, ORDINARY, MessageSid=ORDINARY_SID)
        job = take(client)
        assert finish(client, job, outcome).json()["status"] == outcome
        assert take(client) is None  # nothing is queued again by itself


def test_a_stale_or_wrong_report_is_refused(tmp_path):
    with TestClient(create_app(imessage(tmp_path), stt_factory=None)) as client:
        submit(client, ORDINARY, MessageSid=ORDINARY_SID)
        job = take(client)
        wrong = client.post(f"/integrations/photon/outbox/{job['job_id']}/result",
                            json={"attempt_id": "not-the-attempt", "outcome": "sent"}, headers=BRIDGE)
        assert wrong.status_code == 409
        assert client.post("/integrations/photon/outbox/nobody/result",
                           json={"attempt_id": "x", "outcome": "sent"}, headers=BRIDGE).status_code == 404
        assert detail(client, ORDINARY_ID)["status"] == "sending"


def test_only_the_bridge_token_may_take_or_report(tmp_path):
    with TestClient(create_app(imessage(tmp_path), stt_factory=None)) as client:
        submit(client, ORDINARY, MessageSid=ORDINARY_SID)
        for headers in ({}, CAREGIVER, {"Authorization": "Bearer wrong"}):
            assert client.post("/integrations/photon/outbox/claim", json={"kind": "senior_imessage"},
                               headers=headers).status_code == 401
        assert take(client)["kind"] == "senior_imessage"


def test_the_sms_transport_cannot_report_on_an_imessage_job_nor_the_reverse(tmp_path):
    app = create_app(imessage(tmp_path), stt_factory=None)
    with TestClient(app) as client:
        submit(client, ORDINARY, MessageSid=ORDINARY_SID)
        job = take(client)
        sms_report = lambda: app.state.gateway.store.complete(
            job["job_id"], SendResult(attempt_id=job["attempt_id"], outcome="sent"), kind="relay")
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as refused:
            client.portal.call(sms_report)
        assert refused.value.status_code == 403
    sms = create_app(settings(tmp_path / "sms"), stt_factory=None)
    with TestClient(sms) as client:
        submit(client, ORDINARY, MessageSid=ORDINARY_SID)
        sms_job = relay(client, sms)
        assert finish(client, sms_job).status_code == 403  # the Photon token cannot report on an SMS job


# --- what must hold before anything is sent ---------------------------------------


@pytest.mark.parametrize("change", [dict(TEXT_FORWARDING_ENABLED="false"), dict(DRY_RUN_ACTIONS="true")], ids=["off", "practice"])
def test_nothing_is_taken_while_forwarding_is_off_or_the_server_is_in_practice_mode(tmp_path, change):
    with TestClient(create_app(imessage(tmp_path, **change), stt_factory=None)) as client:
        submit(client, ORDINARY, MessageSid=ORDINARY_SID)
        assert take(client) is None
        assert detail(client, ORDINARY_ID)["status"] == "queued"


def test_a_scam_is_not_sent_until_the_caregiver_releases_it(tmp_path):
    with TestClient(create_app(imessage(tmp_path), stt_factory=None)) as client:
        assert submit(client)["status"] == "held"
        assert take(client) is None  # held texts are not for the senior
        assert decide(client, "release").json()["status"] == "queued"
        job = take(client)
        assert job["recipient"] == SENIOR and "Released after caregiver review." in job["text"]
        assert "Passed automated screening" not in job["text"]
        assert finish(client, job).json()["status"] == "sent"


def test_a_discarded_scam_never_reaches_the_agent(tmp_path):
    with TestClient(create_app(imessage(tmp_path), stt_factory=None)) as client:
        submit(client)
        assert decide(client, "discard").json()["status"] == "discarded"
        assert take(client) is None
        assert "text" not in detail(client, MID)


def test_the_agent_still_gets_caregiver_alerts_in_both_modes(tmp_path):
    for mode in (imessage(tmp_path / "i"), settings(tmp_path / "s")):
        with TestClient(create_app(mode, stt_factory=None)) as client:
            submit(client)
            alert = take(client, "caregiver_alert")
            assert alert["kind"] == "caregiver_alert" and alert["recipient"] == mode.photon_caregiver_address
            assert SCAM not in alert["text"]  # an alert never carries the text's words


# --- settings ------------------------------------------------------------------------


@pytest.mark.parametrize("change,match", [
    (dict(TEXT_FORWARD_VIA="carrier-pigeon"), "sms or imessage"),
    (dict(SENIOR_IMESSAGE_ADDRESS=LINE), "must differ"),
    (dict(SENIOR_IMESSAGE_ADDRESS="+15555550102"), "must differ"),  # the caregiver
])
def test_a_wrong_forwarding_setting_leaves_texts_off_not_the_server(tmp_path, change, match):
    conf = imessage(tmp_path, **{})
    from dataclasses import replace
    values = {"TEXT_FORWARD_VIA": conf.text_forward_via, "SENIOR_IMESSAGE_ADDRESS": conf.senior_imessage_address, **change}
    conf = replace(conf, text_forward_via=values["TEXT_FORWARD_VIA"].lower(),
                   senior_imessage_address=values["SENIOR_IMESSAGE_ADDRESS"])
    with pytest.raises(ValueError, match=match):
        GatewayStore(conf, EventBus()).close()
    app = create_app(conf, stt_factory=None)
    with TestClient(app) as client:
        health = client.get("/health").json()
        assert health["ok"] and health["gateway"]["enabled"] is False and match in health["gateway"]["error"]


def test_the_senior_address_defaults_to_the_senior_phone(tmp_path):
    with TestClient(create_app(settings(tmp_path, TEXT_FORWARD_VIA="imessage", DRY_RUN_ACTIONS="false"), stt_factory=None)) as client:
        submit(client, ORDINARY, MessageSid=ORDINARY_SID)
        assert take(client)["recipient"] == "+15555550101"


def test_a_text_queued_before_the_transport_was_changed_goes_out_the_new_way(tmp_path):
    key = Fernet.generate_key().decode()  # the same queue is opened twice, so it needs the same key
    first = create_app(settings(tmp_path, GATEWAY_ENCRYPTION_KEY=key), stt_factory=None)  # SMS
    with TestClient(first) as client:
        submit(client, ORDINARY, MessageSid=ORDINARY_SID)
    second = create_app(imessage(tmp_path, GATEWAY_ENCRYPTION_KEY=key), stt_factory=None)  # now iMessage
    with TestClient(second) as client:
        assert relay(client, second) is None
        assert take(client)["message_id"] == ORDINARY_ID


# --- what the server says and keeps --------------------------------------------------


def test_health_says_how_forwarding_goes_and_whether_it_could_right_now(tmp_path):
    with TestClient(create_app(imessage(tmp_path), stt_factory=None)) as client:
        gateway = client.get("/health").json()["gateway"]
        assert gateway["forward_via"] == "imessage" and gateway["delivery_provider"] == "photon"
        assert gateway["forwarding_ready"] is False  # the agent has not checked in
        client.post("/integrations/photon/heartbeat", json={"connected": True}, headers=BRIDGE)
        assert client.get("/health").json()["gateway"]["forwarding_ready"] is True
    with TestClient(create_app(settings(tmp_path / "sms"), stt_factory=None)) as client:
        gateway = client.get("/health").json()["gateway"]
        assert gateway["forward_via"] == "sms" and gateway["forwarding_ready"] == gateway["sms_ready"]


def test_the_senior_gets_the_text_but_events_and_disk_never_hold_it(tmp_path):
    conf = imessage(tmp_path)
    app = create_app(conf, stt_factory=None)
    with TestClient(app) as client:
        submit(client, ORDINARY, MessageSid=ORDINARY_SID)
        job = take(client)
        finish(client, job)
        events = json.dumps(app.state.bus.recent())
        assert ORDINARY not in events and SENIOR not in events and SENDER not in events
    on_disk = (conf.log_dir / "events.jsonl").read_text()
    db = (conf.log_dir / "gateway/queue.sqlite3").read_bytes()
    for value in (ORDINARY, SENDER, SENIOR):
        assert value not in on_disk and value.encode() not in db


def test_texts_from_the_senior_imessage_address_are_ignored_like_the_senior_phone(tmp_path):
    from tests.test_server import signed_post
    from tests.test_gateway import form
    conf = imessage(tmp_path, SENIOR_IMESSAGE_ADDRESS="+15555550177")
    with TestClient(create_app(conf, stt_factory=None)) as client:
        assert signed_post(client, "/sms/incoming", form(From="+15555550177")).status_code == 200
        assert client.get("/gateway/messages", headers=CAREGIVER).json()["messages"] == []


def test_a_job_of_the_other_transport_is_never_handed_out_even_if_one_exists(tmp_path):
    """The two kinds never share a queue (a change of transport re-kinds what waits), but should one turn up
    anyway, the transport that is not in use must not take it."""
    for mode, stray, taker in (("sms", "senior_imessage", lambda c, a: take(c)),
                               ("imessage", "relay", lambda c, a: relay(c, a))):
        conf = settings(tmp_path / mode) if mode == "sms" else imessage(tmp_path / mode)
        app = create_app(conf, stt_factory=None)
        with TestClient(app) as client:
            assert submit(client, ORDINARY, MessageSid=ORDINARY_SID)["status"] == "queued"
            store = app.state.gateway.store

            def plant():
                store.db.execute("UPDATE outbox SET kind=? WHERE message_id=? AND kind IN ('relay','senior_imessage')",
                                 (stray, ORDINARY_ID))
                store.db.commit()

            client.portal.call(plant)
            assert taker(client, app) is None, mode
            assert detail(client, ORDINARY_ID)["status"] == "queued", mode
