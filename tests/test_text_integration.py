"""How text protection fits the rest of Palisade: its name, and who may see held texts."""

import pytest
from fastapi.testclient import TestClient

from auth import EMAIL_CLAIM
from gateway import forwarded_sms
from main import create_app
from tests.test_auth import AUDIENCE, DOMAIN, auth0, token
from tests.test_auth import CAREGIVER as CAREGIVER_EMAIL
from tests.test_gateway import BRIDGE, CAREGIVER, MID, SCAM, SENDER, claim, decide, settings, submit


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
    """Once caregivers sign in, the shared token can go. With neither, there is nobody to review: it does not start."""
    sign_in_only = dict(EVENTS_TOKEN="", AUTH0_DOMAIN=DOMAIN, AUTH0_AUDIENCE=AUDIENCE, CAREGIVER_EMAILS=CAREGIVER_EMAIL)
    app = create_app(settings(tmp_path / "sign-in", **sign_in_only), stt_factory=None, sign_in=auth0())
    with TestClient(app) as client:
        assert client.get("/gateway/messages").status_code == 401
        assert client.get("/gateway/messages", headers=CAREGIVER).status_code == 401  # the old token is gone
        assert client.get("/gateway/messages", headers=signed_in()).status_code == 200
    with pytest.raises(ValueError, match="requires"):
        create_app(settings(tmp_path / "neither", EVENTS_TOKEN=""), stt_factory=None)


def test_a_held_text_is_never_fetched_with_the_token_in_the_address(tmp_path):
    """`?token=` opens the feed for the dashboard's link. It does not open a held text: an address gets logged."""
    with TestClient(create_app(settings(tmp_path), stt_factory=None)) as client:
        assert submit(client)["status"] == "held"
        assert client.get("/calls?token=caregiver-private").status_code == 200
        assert client.get("/gateway/messages?token=caregiver-private").status_code == 401
        assert client.get(f"/gateway/messages/{MID}?token=caregiver-private").status_code == 401
        assert decide(client, "discard").status_code == 200  # with the token in the header, as the app sends it
