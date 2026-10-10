#!/usr/bin/env python3
"""Send one made-up text to a running Palisade server the way Twilio would. No phone needed.

    python tools/fake_text.py "USPS: pay the redelivery fee now at https://parcel-fee.example/pay"
    python tools/fake_text.py "Your appointment is tomorrow at 2pm." --from +15555550142
    python tools/fake_text.py "Look at this photo" --attachment --url http://localhost:8001

The text is signed with TWILIO_AUTH_TOKEN from .env, as Twilio signs its own, so the server
takes it for a real one: it is scored and, with text protection on, stored and then held or
queued. Watch it turn up in the app's Texts page.

The text is not real, so it must not reach a real phone. This tool will not talk to a
server that is set to forward texts: run that one with TEXT_FORWARDING_ENABLED=false or
DRY_RUN_ACTIONS=true.
"""

from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import httpx  # noqa: E402

from config import Settings  # noqa: E402
from events import mask_phone  # noqa: E402
from fake_call import signed_post  # noqa: E402


def would_forward(health: dict) -> bool:
    """True when a text this server lets through would be sent on to the protected person's phone."""
    gateway = health.get("gateway") or {}
    return bool(gateway.get("enabled") and gateway.get("forwarding_enabled") and gateway.get("sms_ready"))


def send(client, settings: Settings, base_url: str, text: str, *, sender: str, attachment: bool = False) -> int:
    """Posts the text to `base_url`. Returns the exit code, having said what happened."""
    try:
        health = client.get(base_url + "/health").json()
    except httpx.HTTPError as exc:
        print(f"No Palisade server at {base_url} ({exc}). Start it with: uvicorn main:app --port 8000", file=sys.stderr)
        return 1
    if would_forward(health):
        print(
            "That server forwards texts to a real phone, and this text is made up. Nothing was sent.\n"
            "Run the server with TEXT_FORWARDING_ENABLED=false or DRY_RUN_ACTIONS=true to try texts on it.",
            file=sys.stderr,
        )
        return 1
    form = {
        "MessageSid": "SM" + secrets.token_hex(16),
        "From": sender,
        "To": settings.twilio_proxy_number,
        "Body": text,
        "NumMedia": "1" if attachment else "0",
    }
    response = signed_post(client, settings, base_url, "/sms/incoming", form)
    if response.status_code != 200:
        print(f"/sms/incoming returned HTTP {response.status_code}: {response.text[:200]}", file=sys.stderr)
        return 1
    protected = (health.get("gateway") or {}).get("enabled")
    print(
        f"Sent as a text from {mask_phone(sender)}. "
        + ("It is stored, and held or queued." if protected else "Text protection is off there, so it is only scored.")
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("text", help="what the text says")
    parser.add_argument("--url", default="http://localhost:8000", help="where the Palisade server is listening")
    parser.add_argument("--from", dest="sender", default="+15555550123", help="sender number to present")
    parser.add_argument("--attachment", action="store_true", help="say the text carries a picture or a file")
    args = parser.parse_args()
    with httpx.Client(timeout=10) as client:
        return send(
            client, Settings.from_env(), args.url.rstrip("/"), args.text, sender=args.sender, attachment=args.attachment
        )


if __name__ == "__main__":
    sys.exit(main())
