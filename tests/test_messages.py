import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from main import create_app
from scorer.llm import LLMScorer, ScorerPrompt
from scorer.messages import MessageScorer
from tests.test_llm import FakeProvider
from tests.test_server import HOST, make_settings, signed_post
from tools import set_webhook


SCAM = "USPS: your package is held. Pay the redelivery fee immediately at https://parcel-fee.example/pay"


@pytest.mark.parametrize("text", [
    SCAM,
    "Your bank account is locked. Verify your account immediately at https://bank-check.example/login",
    "Buy gift cards and send me the numbers. Do not tell your family.",
])
def test_message_rules_detect_scam_requests(text):
    result = asyncio.run(MessageScorer().evaluate(text))
    assert result["risk"] >= 60 and result["flags"]


@pytest.mark.parametrize("text", [
    "Your appointment is tomorrow at 2pm. Reply C to confirm.",
    "Your verification code is 123456. Never share this code.",
    "Your verification code is 123456. Never share this code with anyone.",
    "Your security code is 123456. Do not share your security code with anybody.",
    "Your one-time code is 123456. Don't share this code with anyone.",
    "Track your package at https://delivery.example/track/123",
    "Your statement is ready. Open your banking app to view it.",
    "We will never ask for your password. Beware of scams.",
])
def test_message_rules_keep_ordinary_texts_low(text):
    assert asyncio.run(MessageScorer().evaluate(text))["risk"] < 30


@pytest.mark.parametrize("text,flag", [
    ("Never share this code with anyone. Send me your verification code.", "OTP_REQUEST"),
    ("Never share this code with anyone. Do not tell your family about this payment.", "SECRECY"),
])
def test_code_privacy_notice_does_not_hide_other_scam_instructions(text, flag):
    result = asyncio.run(MessageScorer().evaluate(text))
    assert result["risk"] >= 30 and flag in result["flags"]


def test_low_model_score_cannot_lower_deterministic_text_risk():
    provider = FakeProvider(reply=json.dumps({"risk": 0, "flags": [], "explanation": "Looks safe"}))
    llm = LLMScorer("test", ScorerPrompt.load("prompts/message-scorer-prompt.md"), provider=provider, early_exit=False)
    result = asyncio.run(MessageScorer(llm).evaluate(SCAM))
    assert result["risk"] == result["rule_risk"] >= 70
    assert result["llm_risk"] == 0


def test_message_llm_uses_sms_prompt_and_falls_back_on_timeout():
    prompt = ScorerPrompt.load("prompts/message-scorer-prompt.md")
    provider = FakeProvider(delay=0.05)
    scorer = MessageScorer(LLMScorer("test", prompt, provider=provider, timeout_s=0.001, early_exit=False))
    result = asyncio.run(scorer.evaluate(SCAM))
    assert result["llm_error"] == "timeout"
    assert result["risk"] >= 70 and result["llm_risk"] is None
    assert "single SMS" in provider.calls[0]["system"]


def test_model_result_is_combined_but_model_advice_is_not_used():
    provider = FakeProvider(reply=json.dumps({"risk": 95, "flags": ["SECRECY"], "explanation": "Untrusted advice"}))
    llm = LLMScorer("test", ScorerPrompt.load("prompts/message-scorer-prompt.md"), provider=provider, early_exit=False)
    result = asyncio.run(MessageScorer(llm).evaluate("Please do this privately."))
    assert result["risk"] == 95 and "SECRECY" in result["flags"]
    assert result["explanation"] != "Untrusted advice"


def test_manual_check_requires_token_and_valid_text(tmp_path):
    app = create_app(make_settings(tmp_path, EVENTS_TOKEN="private"), stt_factory=None)
    with TestClient(app) as client:
        assert client.post("/messages/analyze", json={"text": SCAM}).status_code == 401
        assert client.get("/messages").status_code == 401
        headers = {"Authorization": "Bearer private"}
        for text in ["", "   ", "x" * 10001]:
            assert client.post("/messages/analyze", json={"text": text}, headers=headers).status_code == 422
        result = client.post("/messages/analyze", json={"text": SCAM}, headers=headers).json()
        assert result["source"] == "manual" and result["risk"] >= 70
        assert client.get("/messages", headers=headers).json()["messages"][0]["message_id"] == result["message_id"]
        assert client.get("/calls", headers=headers).json()["calls"] == []


def message_form(**changes):
    return {"MessageSid": "SM" + "a" * 32, "From": "+14045550142", "To": "+15555550100", "Body": SCAM,
            "NumMedia": "0", **changes}


def test_sms_webhook_checks_signature_destination_and_body(tmp_path):
    app = create_app(make_settings(tmp_path), stt_factory=None)
    with TestClient(app) as client:
        assert client.post("/sms/incoming", data=message_form()).status_code == 403
        assert signed_post(client, "/sms/incoming", message_form(), token="wrong").status_code == 403
        for changes in [{"To": "+15555550999"}, {"MessageSid": ""}, {"Body": ""}, {"Body": "x" * 10001}]:
            assert signed_post(client, "/sms/incoming", message_form(**changes)).status_code == 400
        assert client.get("/messages").json() == {"messages": []}


def test_sms_results_survive_restart_without_bodies_or_duplicate_events(tmp_path):
    settings = make_settings(tmp_path)
    app = create_app(settings, stt_factory=None, twilio_client_factory=lambda: pytest.fail("Must not send SMS or control calls"))
    with TestClient(app) as client:
        for _ in range(2):
            response = signed_post(client, "/sms/incoming", message_form())
            assert response.status_code == 200 and response.text == "<Response></Response>"
        results = client.get("/messages").json()["messages"]
        assert len(results) == 1 and results[0]["sender"] == "***0142"
        assert results[0]["source"] == "twilio" and not results[0]["text_only"]
        assert len([e for e in app.state.bus.recent() if e["type"] == "message_scored"]) == 1
        assert SCAM not in json.dumps(app.state.bus.recent())
        assert not any(e["type"] == "action" for e in app.state.bus.recent())
    persisted = (tmp_path / "logs/events.jsonl").read_text()
    for sensitive in [SCAM, "parcel-fee.example", "4045550142", '"text"', '"body"', '"evidence"']:
        assert sensitive not in persisted
    restarted = create_app(settings, stt_factory=None)
    with TestClient(restarted) as client:
        assert len(client.get("/messages").json()["messages"]) == 1
        assert signed_post(client, "/sms/incoming", message_form()).status_code == 200
        assert not restarted.state.bus.recent()  # replayed webhook did not rescore


def test_mms_caption_is_marked_as_text_only(tmp_path):
    with TestClient(create_app(make_settings(tmp_path), stt_factory=None)) as client:
        assert signed_post(client, "/sms/incoming", message_form(NumMedia="1")).status_code == 200
        assert client.get("/messages").json()["messages"][0]["text_only"] is True


def test_sms_webhook_setup_preserves_voice_settings(monkeypatch):
    updated = []
    number = SimpleNamespace(capabilities={"sms": True}, sms_url="https://old.example/sms",
                             update=lambda **params: updated.append(params))
    client = SimpleNamespace(incoming_phone_numbers=SimpleNamespace(list=lambda **params: [number]))
    settings = SimpleNamespace(twilio_account_sid="ACtest", twilio_auth_token="test",
                               twilio_proxy_number="+15555550100", public_host=HOST)
    monkeypatch.setattr(set_webhook.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(set_webhook, "Client", lambda *args: client)
    monkeypatch.setattr("sys.argv", ["set_webhook.py", "--sms-only"])
    assert set_webhook.main() == 0
    assert updated == [{"sms_url": f"https://{HOST}/sms/incoming", "sms_method": "POST"}]
