"""Isolated gateway UI demo. No Photon/Twilio/LLM credentials or real sends.

Run from the repo: .venv/bin/python tools/demo_gateway.py
Use http://127.0.0.1:8877 and token demo-caregiver in the simulator.
Queued messages are deliberately not sent: no transport service is connected.
"""
import asyncio
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cryptography.fernet import Fernet
import uvicorn

from config import Settings
from gateway import GatewayMessage
from main import create_app


def demo_app():
    settings = Settings.from_env({
        "LOG_DIR": tempfile.mkdtemp(prefix="shield-gateway-demo-"),
        "SCORER_MODEL": "rules", "DRY_RUN_ACTIONS": "true",
        "EVENTS_TOKEN": "demo-caregiver", "TEXT_GATEWAY_ENABLED": "true",
        "TEXT_FORWARDING_ENABLED": "false", "PHOTON_BRIDGE_TOKEN": "demo-bridge",
        "TWILIO_PROXY_NUMBER": "+15555550900", "SENIOR_PHONE": "+15555550101",
        "CAREGIVER_PHONE": "+15555550102", "GATEWAY_ENCRYPTION_KEY": Fernet.generate_key().decode(),
    })
    app = create_app(settings, stt_factory=None, llm=None, message_llm=None)
    async def seed():
        await app.state.gateway.ingest(GatewayMessage(
            message_id="demo_held", sender="+15555550999", line=settings.twilio_proxy_number,
            text="[DEMO] USPS: Pay the redelivery fee immediately at https://parcel-fee.example/pay",
        ))
        await app.state.gateway.ingest(GatewayMessage(
            message_id="demo_allowed", sender="+15555550998", line=settings.twilio_proxy_number,
            text="[DEMO] Your appointment is tomorrow at 2pm.",
        ))
    asyncio.run(seed())
    return app


if __name__ == "__main__":
    print("Isolated gateway demo: no real messages are sent. Forwarding is paused.")
    uvicorn.run(demo_app(), host="127.0.0.1", port=8877, log_level="warning")
