"""How text protection fits the rest of Palisade: its name, and who may see held texts."""

from fastapi.testclient import TestClient

from auth import EMAIL_CLAIM
from gateway import forwarded_sms
from main import create_app
from tests.test_auth import AUDIENCE, DOMAIN, auth0, token
from tests.test_auth import CAREGIVER as CAREGIVER_EMAIL
from tests.test_gateway import BRIDGE, CAREGIVER, MID, SCAM, SENDER, claim, decide, form, settings, submit
from tests.test_server import incoming, signed_post, wait_for


def signed_in(**claims) -> dict:
    return {"Authorization": f"Bearer {token(**claims)}"}


def test_what_people_read_says_palisade(tmp_path):
    """The forwarded text's footer, the caregiver's alert and the answer to WHY carry the product's name."""
    footer = forwarded_sms("Hello", SENDER)
    assert "— Palisade screening —" in footer
    with TestClient(create_app(settings(tmp_path), stt_factory=None)) as client:
        assert submit(client)["status"] == "held"
        alert = claim(client)["text"]
        why = client.get("/integrations/photon/status", headers=BRIDGE).json()["why"]
    assert alert.startswith("Palisade held a text") and "Palisade app's Texts tab" in alert
    assert why.startswith("Palisade detected")
    assert "Shield" not in footer + alert + why


# --- who may see held texts ----------------------------------------------------


def test_a_signed_in_caregiver_can_review_held_texts(tmp_path):
    """Held texts take the same check as calls: the access token, or a caregiver signed in with Auth0."""
    app = create_app(settings(tmp_path), stt_factory=None, sign_in=auth0())
    stranger = signed_in(**{EMAIL_CLAIM: "stranger@example.com"})
    with TestClient(app) as client:
        assert submit(client)["status"] == "held"
        assert client.get("/gateway/messages", headers=signed_in()).status_code == 200
        assert client.get(f"/gateway/messages/{MID}", headers=signed_in()).json()["text"] == SCAM
        assert client.get("/gateway/messages", headers=CAREGIVER).status_code == 200  # the token still opens it
        # Signed in, but not on the caregiver list: nothing of a held text, and no say over it.
        assert client.get("/gateway/messages", headers=stranger).status_code == 403
        assert client.get(f"/gateway/messages/{MID}", headers=stranger).status_code == 403
        refused = client.post(f"/gateway/messages/{MID}/decision", json={"action": "release"}, headers=stranger)
        assert refused.status_code == 403
        assert client.get(f"/gateway/messages/{MID}", headers=signed_in()).json()["status"] == "held"
        released = client.post(f"/gateway/messages/{MID}/decision", json={"action": "release"}, headers=signed_in())
        assert released.json()["status"] == "queued"
        # The Photon bridge's routes are not the caregiver's: signing in opens none of them.
        assert client.post("/integrations/photon/outbox/claim", headers=signed_in()).status_code == 401


def test_text_protection_can_rest_on_sign_in_alone(tmp_path):
    """Once caregivers sign in, the shared token can go. With neither, there is nobody to review: texts stay off."""
    sign_in_only = dict(EVENTS_TOKEN="", AUTH0_DOMAIN=DOMAIN, AUTH0_AUDIENCE=AUDIENCE, CAREGIVER_EMAILS=CAREGIVER_EMAIL)
    app = create_app(settings(tmp_path / "sign-in", **sign_in_only), stt_factory=None, sign_in=auth0())
    with TestClient(app) as client:
        assert client.get("/gateway/messages").status_code == 401
        assert client.get("/gateway/messages", headers=CAREGIVER).status_code == 401  # the old token is gone
        assert client.get("/gateway/messages", headers=signed_in()).status_code == 200
    neither = create_app(settings(tmp_path / "neither", EVENTS_TOKEN=""), stt_factory=None)
    assert neither.state.gateway is None


def test_a_held_text_is_never_fetched_with_the_token_in_the_address(tmp_path):
    """`?token=` opens the feed for the dashboard's link. It does not open a held text: an address gets logged."""
    with TestClient(create_app(settings(tmp_path), stt_factory=None)) as client:
        assert submit(client)["status"] == "held"
        assert client.get("/calls?token=caregiver-private").status_code == 200
        assert client.get("/gateway/messages?token=caregiver-private").status_code == 401
        assert client.get(f"/gateway/messages/{MID}?token=caregiver-private").status_code == 401
        assert decide(client, "discard").status_code == 200  # with the token in the header, as the app sends it


# --- a wrong text setting ------------------------------------------------------


def test_a_wrong_text_setting_turns_texts_off_and_leaves_calls_on(tmp_path, caplog):
    """Text protection is extra. A mistyped key must not stop the server that screens the calls."""
    conf = settings(tmp_path, GATEWAY_ENCRYPTION_KEY="not-a-key")
    with caplog.at_level("ERROR", logger="shield"):
        app = create_app(conf, stt_factory=None)
    assert "Text protection is OFF: GATEWAY_ENCRYPTION_KEY must be a Fernet key" in caplog.text
    with TestClient(app) as client:
        # A call is answered and connected as on any other day.
        answer = incoming(client)
        assert answer.status_code == 200 and "<Dial" in answer.text
        health = client.get("/health").json()
        assert health["gateway"] == {"enabled": False, "error": "GATEWAY_ENCRYPTION_KEY must be a Fernet key"}
        # A text is still scored, the way a server with text protection off scores it. Nothing
        # is kept of it and nothing is queued for anyone.
        assert signed_post(client, "/sms/incoming", form()).status_code == 200
        assert client.get("/gateway/messages", headers=CAREGIVER).status_code == 503
        results = client.get("/messages", headers=CAREGIVER).json()["messages"]
        assert len(results) == 1 and results[0]["risk"] >= 70 and "text" not in results[0]
    assert not (conf.log_dir / "gateway").exists()


def test_health_names_no_error_when_text_protection_is_simply_off(tmp_path):
    app = create_app(settings(tmp_path, TEXT_GATEWAY_ENABLED="false"), stt_factory=None)
    with TestClient(app) as client:
        assert client.get("/health").json()["gateway"] == {"enabled": False}


# --- tools/fake_text.py ----------------------------------------------------------


def test_a_made_up_text_is_taken_for_a_real_one(tmp_path, capsys):
    from tools import fake_text

    conf = settings(tmp_path, TEXT_FORWARDING_ENABLED="false")
    with TestClient(create_app(conf, stt_factory=None)) as client:
        assert fake_text.send(client, conf, "", SCAM, sender=SENDER) == 0
        assert fake_text.send(client, conf, "", "A photo for you", sender="+15555550177", attachment=True) == 0
        assert wait_for(lambda: all(m["status"] == "held" for m in listed(client)) and len(listed(client)) == 2)
        assert {m["hold_reason"] for m in listed(client)} == {"scam_signals", "unsupported_content"}
    said = capsys.readouterr().out
    assert "from ***0999" in said and SENDER not in said  # the tool masks numbers like everything else


def test_a_made_up_text_is_never_sent_to_a_server_that_would_forward_it(tmp_path, capsys):
    """Forwarding is on and the server can send: a text it lets through reaches a real phone."""
    from tools import fake_text

    conf = settings(tmp_path, DRY_RUN_ACTIONS="false")
    sent = []
    app = create_app(conf, stt_factory=None, twilio_client_factory=lambda: sent.append("client") or None)
    with TestClient(app) as client:
        assert client.get("/health").json()["gateway"]["sms_ready"] is True
        assert fake_text.send(client, conf, "", "Your appointment is tomorrow at 2pm.", sender=SENDER) == 1
        assert listed(client) == [] and sent == []
    assert "Nothing was sent" in capsys.readouterr().err


def listed(client) -> list:
    return client.get("/gateway/messages", headers=CAREGIVER).json()["messages"]
