#!/usr/bin/env python3
"""Play an eval script into a running Shield server the way Twilio would. No phone needed.

    python tools/fake_call.py scam_01_benefits_suspension
    python tools/fake_call.py legit_01_pharmacy_refill --url http://localhost:8000
    python tools/fake_call.py --list

Each turn is spoken with the macOS `say` voice, converted to mu-law 8 kHz, and streamed
in real time as Twilio Media Stream frames: the caller on the inbound track, the senior
on the outbound track. With DEEPGRAM_API_KEY set on the server you get real transcripts,
scores and actions. Without it the server only counts the audio frames.

The call is not a real Twilio call, so run the server with DRY_RUN_ACTIONS=true:
otherwise ending the call fails against Twilio and a real SMS is sent to CAREGIVER_PHONE.
"""

from __future__ import annotations

import argparse
import array
import asyncio
import base64
import json
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import httpx  # noqa: E402
from twilio.request_validator import RequestValidator  # noqa: E402
from websockets.asyncio.client import connect  # noqa: E402

from config import Settings  # noqa: E402
from watch import describe, read_events  # noqa: E402

FRAME_BYTES = 160  # 20 ms of mu-law at 8 kHz, the size Twilio sends
SILENCE = 0xFF
TAIL_S = 4.0  # keep the line open after the last word so it can be transcribed and scored


def _mulaw_table() -> bytes:
    """16-bit linear PCM -> 8-bit mu-law (G.711), indexed by the sample as an unsigned 16-bit value."""
    table = bytearray(65536)
    for index in range(65536):
        sample = index - 65536 if index >= 32768 else index
        sign = 0x80 if sample < 0 else 0
        magnitude = min(abs(sample), 32635) + 0x84
        exponent = 7
        mask = 0x4000
        while exponent > 0 and not magnitude & mask:
            exponent -= 1
            mask >>= 1
        mantissa = (magnitude >> (exponent + 3)) & 0x0F
        table[index] = ~(sign | (exponent << 4) | mantissa) & 0xFF
    return bytes(table)


def speak(text: str, workdir: Path, name: str, table: bytes) -> bytes:
    """Text -> mu-law 8 kHz audio, using the macOS speech synthesizer."""
    script, aiff, wav = workdir / f"{name}.txt", workdir / f"{name}.aiff", workdir / f"{name}.wav"
    script.write_text(text, encoding="utf-8")
    subprocess.run(["say", "-f", str(script), "-o", str(aiff)], check=True)
    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@8000", "-c", "1", str(aiff), str(wav)], check=True)
    with wave.open(str(wav)) as audio:
        samples = array.array("h", audio.readframes(audio.getnframes()))
    return bytes(table[sample & 0xFFFF] for sample in samples)


def build_tracks(turns: list[dict]) -> tuple[bytes, bytes, list[tuple[float, str, str]]]:
    """Lay each turn onto its track at the script's timestamp (or right after that speaker's previous turn)."""
    table = _mulaw_table()
    tracks = {"caller": bytearray(), "senior": bytearray()}
    spoken = []
    with tempfile.TemporaryDirectory() as tmp:
        for index, turn in enumerate(turns):
            track = tracks[turn["speaker"]]
            start = max(int(turn["t"] * 8000), len(track))
            track.extend(bytes([SILENCE]) * (start - len(track)))
            track.extend(speak(turn["text"], Path(tmp), f"turn{index}", table))
            spoken.append((start / 8000, turn["speaker"].upper(), turn["text"]))
    length = max(len(track) for track in tracks.values()) + int(TAIL_S * 8000)
    length += -length % FRAME_BYTES
    for track in tracks.values():
        track.extend(bytes([SILENCE]) * (length - len(track)))
    return bytes(tracks["caller"]), bytes(tracks["senior"]), spoken


def signed_post(client: httpx.Client, settings: Settings, base_url: str, path: str, params: dict) -> httpx.Response:
    """POST a webhook signed the way Twilio signs it, so signature validation can stay on."""
    public = f"https://{settings.public_host}" if settings.public_host else base_url
    signature = RequestValidator(settings.twilio_auth_token).compute_signature(public + path, params)
    return client.post(base_url + path, data=params, headers={"X-Twilio-Signature": signature})


def print_events(base_url: str, token: str, call_sid: str, ended: threading.Event) -> None:
    try:
        for event in read_events(base_url, token):
            if event.get("call_sid") not in (call_sid, None):
                continue
            line = describe(event)
            if line is not None and event.get("type") != "media":
                print(line, flush=True)
            if event.get("type") == "action" and event.get("kind") == "END":
                ended.set()
            if event.get("type") == "call_ended":
                return
    except httpx.HTTPError as exc:
        print(f"(event feed unavailable: {exc})", file=sys.stderr)


async def stream_call(ws_url: str, call_sid: str, token: str, caller: bytes, senior: bytes, ended: threading.Event) -> None:
    async with connect(ws_url) as ws:
        await ws.send(json.dumps({"event": "connected", "protocol": "Call", "version": "1.0.0"}))
        await ws.send(
            json.dumps(
                {
                    "event": "start",
                    "streamSid": "MZfake",
                    "start": {
                        "callSid": call_sid,
                        "streamSid": "MZfake",
                        "tracks": ["inbound", "outbound"],
                        "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1},
                        "customParameters": {"token": token},
                    },
                }
            )
        )
        started = time.monotonic()
        hang_up_at = None
        for index in range(len(caller) // FRAME_BYTES):
            if ended.is_set() and hang_up_at is None:
                hang_up_at = time.monotonic() + 1.0  # a real call drops shortly after Shield ends it
            if hang_up_at is not None and time.monotonic() >= hang_up_at:
                break
            chunk = slice(index * FRAME_BYTES, (index + 1) * FRAME_BYTES)
            for track, audio in (("inbound", caller), ("outbound", senior)):
                payload = base64.b64encode(audio[chunk]).decode()
                await ws.send(
                    json.dumps(
                        {
                            "event": "media",
                            "streamSid": "MZfake",
                            "media": {"track": track, "chunk": str(index + 1), "timestamp": str(index * 20), "payload": payload},
                        }
                    )
                )
            await asyncio.sleep(max(0.0, started + (index + 1) * 0.02 - time.monotonic()))
        await ws.send(json.dumps({"event": "stop", "streamSid": "MZfake", "stop": {"callSid": call_sid}}))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("script", nargs="?", help="script id from eval/eval-scripts.json (a unique part of it is enough)")
    parser.add_argument("--url", default="http://localhost:8000", help="where the Shield server is listening")
    parser.add_argument("--from", dest="caller", default="+15555550123", help="caller number to present")
    parser.add_argument("--list", action="store_true", help="list the available scripts")
    args = parser.parse_args()

    scripts = json.loads((ROOT / "eval" / "eval-scripts.json").read_text(encoding="utf-8"))["scripts"]
    if args.list or not args.script:
        print("\n".join(f"{script['id']:<40} {script['notes']}" for script in scripts))
        return 0
    matches = [script for script in scripts if args.script in script["id"]]
    if len(matches) != 1:
        print(f"{args.script!r} matches {len(matches)} scripts; use --list to see them.", file=sys.stderr)
        return 1
    if not (shutil.which("say") and shutil.which("afconvert")):
        print("This tool needs the macOS `say` and `afconvert` commands.", file=sys.stderr)
        return 1

    settings = Settings.from_env()
    base_url = args.url.rstrip("/")
    call_sid = "CAfake" + secrets.token_hex(13)
    print(f"Synthesizing {len(matches[0]['turns'])} turns of {matches[0]['id']} ...")
    caller_audio, senior_audio, spoken = build_tracks(matches[0]["turns"])

    with httpx.Client(timeout=10) as client:
        try:
            health = client.get(base_url + "/health").json()
        except httpx.HTTPError as exc:
            print(f"No Shield server at {base_url} ({exc}). Start it with: uvicorn main:app --port 8000", file=sys.stderr)
            return 1
        if health.get("actions") == "live":
            print("WARNING: the server is not in dry-run mode. A detection will text CAREGIVER_PHONE for real.")
        response = signed_post(client, settings, base_url, "/voice/incoming", {"CallSid": call_sid, "From": args.caller, "To": settings.twilio_proxy_number})
        if response.status_code != 200:
            print(f"/voice/incoming returned HTTP {response.status_code}: {response.text[:200]}", file=sys.stderr)
            return 1
        token = re.search(r'<Parameter name="token" value="([^"]+)"', response.text)
        if token is None:
            print("The server did not start a media stream for this call (allowlisted caller, or SENIOR_PHONE unset):")
            print(response.text)
            return 0
        signed_post(client, settings, base_url, "/voice/status", {"CallSid": call_sid + "child", "ParentCallSid": call_sid, "CallStatus": "in-progress"})

    print(f"Script (what is being said), stt={health.get('stt')}, scorer={health.get('scorer_model')}:")
    for at, speaker, text in spoken:
        print(f"  {at:5.1f}s  {speaker:<6}  {text}")
    print("\nWhat Shield heard and decided:")
    ended = threading.Event()
    printer = threading.Thread(target=print_events, args=(base_url, settings.events_token, call_sid, ended), daemon=True)
    printer.start()
    time.sleep(0.3)  # let the event feed connect before audio starts
    ws_url = base_url.replace("http", "ws", 1) + "/media"
    asyncio.run(stream_call(ws_url, call_sid, token.group(1), caller_audio, senior_audio, ended))
    printer.join(timeout=20)
    return 0


if __name__ == "__main__":
    sys.exit(main())
