#!/usr/bin/env python3
"""Print Shield's live event feed in a readable form. It stands in for the dashboard for now.

    python tools/watch.py                                   # http://localhost:8000
    python tools/watch.py https://abc.ngrok-free.app --token s3cret
    python tools/watch.py --interim                         # also show in-progress transcript text
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Iterator, Optional

import httpx

RED, YELLOW, DIM, BOLD, RESET = ("\033[31m", "\033[33m", "\033[2m", "\033[1m", "\033[0m") if sys.stdout.isatty() else ("",) * 5


def describe(event: dict, *, interim: bool = False) -> Optional[str]:
    """One printable line for an event, or None to skip it."""
    kind = event.get("type")
    sid = (event.get("call_sid") or "")[-6:]
    if kind == "call_started":
        mode = "monitored" if event.get("monitored") else "allowlisted, not monitored"
        return f"{BOLD}call {sid} from {event.get('caller')} ({mode}){RESET}"
    if kind == "stream_started":
        return f"{DIM}         audio stream up, tracks: {', '.join(event.get('tracks') or [])}{RESET}"
    if kind == "call_answered":
        return f"{DIM}         senior answered{RESET}"
    if kind == "media":
        counts = " ".join(f"{track}={count}" for track, count in (event.get("frames") or {}).items())
        return f"{DIM}         audio frames: {counts}{RESET}"
    if kind == "transcript":
        if not event.get("final") and not interim:
            return None
        text = event.get("text") if event.get("final") else f"{DIM}{event.get('text')} ...{RESET}"
        return f"{event.get('end', 0):7.1f}s  {event.get('speaker', ''):<6}  {text}"
    if kind == "rule_hit":
        return f"{DIM}         rules: +{', +'.join(event.get('flags') or [])} (rule risk {event.get('rule_risk')}){RESET}"
    if kind == "score":
        llm = event.get("llm_risk")
        if llm is not None:
            llm_part = f"llm {llm}, " + ("unchanged" if event.get("llm_reused") else f"{event.get('llm_latency_ms')} ms")
        else:
            llm_part = f"llm unavailable: {event.get('llm_error')}" if event.get("llm_error") else "llm off"
        flags = ", ".join(event.get("flags") or []) or "no flags"
        return f"{event.get('t', 0):7.1f}s  risk {event.get('risk'):>3}  [rules {event.get('rule_risk')} | {llm_part}]  {flags}"
    if kind == "action":
        color = RED if event.get("kind") == "END" else YELLOW
        return f"{color}{BOLD}  >>> {event.get('kind')} (risk {event.get('risk')}): {event.get('reason')}{RESET}"
    if kind == "action_result":
        stages = event.get("stages_s") or {}
        timing = ", ".join(f"{name} {value}" for name, value in stages.items() if value is not None)
        parts = [f"{key}: {event[key]}" for key in ("call_control", "sms") if key in event]
        return f"      {' | '.join(parts)} | {event.get('seconds')} s from utterance to action ({timing})"
    if kind == "sms_status":
        return f"      caregiver SMS {event.get('status')}" + (f" (error {event['error_code']})" if event.get("error_code") else "")
    if kind == "stt_error":
        return f"{RED}         speech-to-text: {event.get('error')}{RESET}"
    if kind == "call_ended":
        return f"{BOLD}call {sid} ended: {event.get('outcome')} after {event.get('duration_s')} s, {event.get('evaluations')} evaluations{RESET}\n"
    return None


def read_events(url: str, token: str = "", *, replay: bool = False) -> Iterator[dict]:
    """Yield events from a Shield server's /events feed until the connection drops."""
    params = {"replay": "true" if replay else "false"}
    if token:
        params["token"] = token
    with httpx.stream("GET", url.rstrip("/") + "/events", params=params, timeout=None) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if line.startswith("data:"):
                yield json.loads(line[5:])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("url", nargs="?", default="http://localhost:8000")
    parser.add_argument("--token", default="", help="EVENTS_TOKEN, if the server has one set")
    parser.add_argument("--interim", action="store_true", help="show in-progress transcript text too")
    parser.add_argument("--replay", action="store_true", help="start with the server's recent events")
    args = parser.parse_args()
    print(f"Watching {args.url} (Ctrl+C to stop)\n")
    try:
        for event in read_events(args.url, args.token, replay=args.replay):
            line = describe(event, interim=args.interim)
            if line is not None:
                print(line, flush=True)
    except KeyboardInterrupt:
        return 0
    except httpx.HTTPError as exc:
        print(f"Could not read {args.url}/events: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
