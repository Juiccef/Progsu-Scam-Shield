"""In-process event bus: fans events out to /events subscribers and appends them to a JSONL log.

Call from the event loop only. Transcript text never reaches the disk: events that
carry it are delivered live, kept in a short replay buffer, and dropped when the call ends.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import deque
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger("shield.events")

MEMORY_ONLY_TYPES = {"transcript"}
MEMORY_ONLY_FIELDS = {"text", "evidence", "explanation"}

_NON_DIGITS = re.compile(r"\D+")


def mask_phone(number: str) -> str:
    """Everything but the last four digits is hidden. Use this for anything logged or published."""
    digits = _NON_DIGITS.sub("", number or "")
    return f"***{digits[-4:]}" if len(digits) >= 4 else "unknown"


class EventBus:
    def __init__(self, log_dir: Optional[Path] = None, *, history: int = 400) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._recent: deque[dict] = deque(maxlen=history)
        self._listeners: list[Callable[[dict], None]] = []
        self._log_file = None
        if log_dir is not None:
            log_dir.mkdir(parents=True, exist_ok=True)
            self._log_file = open(log_dir / "events.jsonl", "a", buffering=1, encoding="utf-8")

    def publish(self, type: str, **data) -> dict:
        event = {"type": type, "ts": round(time.time(), 3), **data}
        self._recent.append(event)
        for listener in self._listeners:
            listener(event)
        for queue in self._subscribers:
            if queue.full():  # a stalled reader loses its oldest event, not the server its memory
                queue.get_nowait()
            queue.put_nowait(event)
        if self._log_file is not None and type not in MEMORY_ONLY_TYPES:
            persisted = {key: value for key, value in event.items() if key not in MEMORY_ONLY_FIELDS}
            self._log_file.write(json.dumps(persisted) + "\n")
        return event

    def add_listener(self, listener: Callable[[dict], None]) -> None:
        """Call `listener` with every event, in the publishing task. It must be quick and must not raise."""
        self._listeners.append(listener)

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def recent(self) -> list[dict]:
        return list(self._recent)

    def forget_call(self, call_sid: str) -> None:
        """Drop a finished call's transcript text from the replay buffer."""
        kept = []
        for event in self._recent:
            if event.get("call_sid") != call_sid:
                kept.append(event)
            elif event["type"] not in MEMORY_ONLY_TYPES:
                kept.append({key: value for key, value in event.items() if key not in MEMORY_ONLY_FIELDS})
        self._recent = deque(kept, maxlen=self._recent.maxlen)

    def close(self) -> None:
        if self._log_file is not None:
            self._log_file.close()
            self._log_file = None
