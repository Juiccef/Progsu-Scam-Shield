#!/usr/bin/env python3
"""Point the Palisade proxy number's voice webhook at this server's public address.

    python tools/set_webhook.py                       # uses PUBLIC_HOST from .env
    python tools/set_webhook.py abc.ngrok-free.app

Changes the number's configuration in your Twilio account. The previous webhook
is printed so you can put it back.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from twilio.rest import Client  # noqa: E402

from config import Settings, _host  # noqa: E402
from events import mask_phone  # noqa: E402


def main() -> int:
    settings = Settings.from_env()
    host = _host(sys.argv[1]) if len(sys.argv) > 1 else settings.public_host
    missing = [
        name
        for name, value in (
            ("TWILIO_ACCOUNT_SID", settings.twilio_account_sid),
            ("TWILIO_AUTH_TOKEN", settings.twilio_auth_token),
            ("TWILIO_PROXY_NUMBER", settings.twilio_proxy_number),
            ("PUBLIC_HOST (or pass the host as an argument)", host),
        )
        if not value
    ]
    if missing:
        print("Missing: " + ", ".join(missing), file=sys.stderr)
        return 1

    client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
    numbers = client.incoming_phone_numbers.list(phone_number=settings.twilio_proxy_number, limit=1)
    if not numbers:
        print(f"No number ending {mask_phone(settings.twilio_proxy_number)} in this Twilio account.", file=sys.stderr)
        return 1
    number = numbers[0]
    print(f"Previous voice webhook: {number.voice_url or '(none)'}")
    number.update(
        voice_url=f"https://{host}/voice/incoming",
        voice_method="POST",
        status_callback=f"https://{host}/voice/status",
        status_callback_method="POST",
    )
    print(f"Voice webhook is now:   https://{host}/voice/incoming")
    if host != settings.public_host:
        print(
            f"The server at that address needs PUBLIC_HOST={host}, or webhook signatures will not validate.\n"
            "tools/vultr_deploy.py sets it on a Vultr server. For a server on this computer, put it in .env and restart."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
