"""Demo calls: play an eval script through the real scoring pipeline with no phone involved.

Off unless DEMO_MODE=true. A demo call is marked `simulated` in its events and in the
call history, never touches Twilio (its actions are always dry-run), and uses the
script's text in place of speech-to-text. Scoring, decisions and timing are the real thing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Awaitable, Callable

from session import CallSession

log = logging.getLogger("shield.demo")

WORDS_PER_SECOND = 2.6
INTERIM_EVERY_S = 0.6  # how often in-progress text is "recognized"
RECOGNITION_DELAY_S = 0.3  # typical lag of streaming speech-to-text
FINAL_DELAY_S = 0.5  # pause before an utterance is finalized
PAUSE_BETWEEN_TURNS_S = 0.4
LINGER_AFTER_LAST_TURN_S = 5.0  # leaves room for a pending confirmation
LINGER_AFTER_END_S = 2.0  # a real call drops shortly after Palisade ends it
DEMO_CALLER = "+15555550123"


def load_scripts(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["scripts"]


def describe(script: dict) -> dict:
    """What a client needs to offer the script in a picker."""
    last = script["turns"][-1]
    return {
        "id": script["id"],
        "label": script["label"],
        "category": script.get("category", ""),
        "notes": script.get("notes", ""),
        "turns": len(script["turns"]),
        "duration_s": round(last["t"] + len(last["text"].split()) / WORDS_PER_SECOND),
    }


async def play(session: CallSession, script: dict, *, speed: float = 1.0) -> None:
    """Speak the script into the session in real time: growing partial text, then the final line."""

    async def pause(seconds: float) -> None:
        await asyncio.sleep(seconds / speed)

    started = time.time()
    free_at = started  # when the line is free for the next speaker
    for turn in script["turns"]:
        if session.scorer.engine.ended:
            break
        speaker = turn["speaker"].upper()
        words = turn["text"].split()
        begin = max(started + turn["t"] / speed, free_at)
        await asyncio.sleep(max(0.0, begin - time.time()))
        spoken = 0
        while spoken < len(words) and not session.scorer.engine.ended:
            await pause(INTERIM_EVERY_S)
            spoken = min(len(words), int((time.time() - begin) * speed * WORDS_PER_SECOND))
            if 0 < spoken < len(words):
                heard_at = begin + spoken / WORDS_PER_SECOND / speed
                await pause(RECOGNITION_DELAY_S)
                session.add_transcript(speaker, " ".join(words[:spoken]), final=False, start_at=begin, end_at=heard_at)
        end = begin + len(words) / WORDS_PER_SECOND / speed
        await asyncio.sleep(max(0.0, end - time.time()))
        await pause(FINAL_DELAY_S)
        session.add_transcript(speaker, turn["text"], final=True, start_at=begin, end_at=end)
        free_at = end + PAUSE_BETWEEN_TURNS_S / speed

    deadline = time.time() + LINGER_AFTER_LAST_TURN_S
    while time.time() < deadline and not session.scorer.engine.ended:
        await asyncio.sleep(0.1)
    if session.scorer.engine.ended:
        await asyncio.sleep(LINGER_AFTER_END_S)


def start(
    session: CallSession, script: dict, *, speed: float, on_finished: Callable[[], Awaitable[None]]
) -> asyncio.Task:
    """Run a demo call in the background and clean up when it is over, however it ends."""

    async def run() -> None:
        try:
            await play(session, script, speed=speed)
        except Exception:
            log.exception("Demo call %s failed", session.call_sid)
        finally:
            await on_finished()

    return asyncio.create_task(run(), name=f"demo-{session.call_sid}")
