"""How text protection fits the rest of Palisade: its name, and who may see held texts."""

from fastapi.testclient import TestClient

from gateway import forwarded_sms
from main import create_app
from tests.test_gateway import BRIDGE, SENDER, claim, settings, submit


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
