"""Deepgram streaming speech-to-text over a raw WebSocket.

Deepgram accepts Twilio's mu-law 8 kHz audio as is, so nothing is transcoded.
Audio waits in a small bounded queue while the socket connects and is never stored.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from bisect import bisect_right
from typing import Optional
from urllib.parse import urlencode

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from . import OnError, OnResult, STTResult

log = logging.getLogger("shield.stt")

URL = "wss://api.deepgram.com/v1/listen"
BYTES_PER_SECOND = 8000  # mu-law, 8 kHz, mono
KEEPALIVE_S = 4.0  # Deepgram closes a socket that receives nothing for about 10 seconds
MARK_EVERY_S = 0.2
MAX_QUEUED_CHUNKS = 500  # 10 seconds of Twilio's 20 ms frames


class DeepgramStream:
    def __init__(
        self,
        api_key: str,
        label: str,
        on_result: OnResult,
        on_error: Optional[OnError] = None,
        *,
        model: str = "nova-3",
        language: str = "en-US",
        endpointing_ms: int = 300,
    ) -> None:
        self._api_key = api_key
        self._label = label
        self._on_result = on_result
        self._on_error = on_error
        self._url = URL + "?" + urlencode(
            {
                "model": model,
                "language": language,
                "encoding": "mulaw",
                "sample_rate": 8000,
                "channels": 1,
                "interim_results": "true",
                "smart_format": "true",
                "endpointing": endpointing_ms,
            }
        )
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=MAX_QUEUED_CHUNKS)
        self._task: Optional[asyncio.Task] = None
        self._closing = False
        self.dropped_chunks = 0
        # Audio position on the current connection -> when that audio reached us.
        self._mark_positions: list[float] = []
        self._mark_times: list[float] = []

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name=f"deepgram-{self._label}")

    def send_audio(self, chunk: bytes) -> None:
        if self._queue.full():
            self._queue.get_nowait()
            self.dropped_chunks += 1
        self._queue.put_nowait((chunk, time.time()))

    async def close(self) -> None:
        self._closing = True
        if self._queue.full():
            self._queue.get_nowait()
        self._queue.put_nowait(None)
        if self._task is None:
            return
        try:
            await asyncio.wait_for(self._task, timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            self._task.cancel()

    async def _run(self) -> None:
        backoff = 0.5
        while not self._closing:
            try:
                async with connect(
                    self._url,
                    additional_headers={"Authorization": f"Token {self._api_key}"},
                    open_timeout=5,
                    ping_interval=None,  # audio and KeepAlive messages already keep the socket alive
                ) as ws:
                    log.info("Deepgram connected track=%s", self._label)
                    backoff = 0.5
                    sender = asyncio.create_task(self._send_loop(ws))
                    sender.add_done_callback(lambda task: task.cancelled() or task.exception())
                    try:
                        async for raw in ws:
                            self._handle(raw)
                    finally:
                        sender.cancel()
            except InvalidStatus as exc:
                status = exc.response.status_code
                hint = " (check DEEPGRAM_API_KEY)" if status in (401, 403) else ""
                self._fail(f"Deepgram refused the connection: HTTP {status}{hint}")
                if status in (400, 401, 402, 403):
                    return  # retrying will not help
            except ConnectionClosed as exc:
                if not self._closing:
                    self._fail(f"Deepgram connection closed: {exc}")
            except OSError as exc:
                self._fail(f"Deepgram connection failed: {exc}")
            if self._closing:
                return
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 5.0)

    async def _send_loop(self, ws) -> None:
        position = 0.0  # seconds of audio sent on this connection
        self._mark_positions, self._mark_times = [], []
        while True:
            try:
                item = await asyncio.wait_for(self._queue.get(), timeout=KEEPALIVE_S)
            except asyncio.TimeoutError:
                await ws.send(json.dumps({"type": "KeepAlive"}))
                continue
            if item is None:
                await ws.send(json.dumps({"type": "CloseStream"}))
                return
            chunk, heard_at = item
            if not self._mark_positions or position - self._mark_positions[-1] >= MARK_EVERY_S:
                self._mark(position, heard_at)
            await ws.send(chunk)
            position += len(chunk) / BYTES_PER_SECOND

    def _mark(self, position: float, heard_at: float) -> None:
        self._mark_positions.append(position)
        self._mark_times.append(heard_at)
        if len(self._mark_positions) > 6000:  # keep roughly the last ten minutes
            del self._mark_positions[:3000]
            del self._mark_times[:3000]

    def _heard_at(self, position: float) -> float:
        """When the audio at `position` seconds into this connection reached us."""
        index = bisect_right(self._mark_positions, position) - 1
        if index < 0:
            return time.time()
        return min(self._mark_times[index] + (position - self._mark_positions[index]), time.time())

    def _handle(self, raw) -> None:
        try:
            message = json.loads(raw)
        except (TypeError, ValueError):
            return
        kind = message.get("type")
        if kind == "Results":
            alternatives = message.get("channel", {}).get("alternatives") or [{}]
            start = float(message.get("start") or 0.0)
            duration = float(message.get("duration") or 0.0)
            self._on_result(
                STTResult(
                    text=(alternatives[0].get("transcript") or "").strip(),
                    is_final=bool(message.get("is_final")),
                    start_at=self._heard_at(start),
                    end_at=self._heard_at(start + duration),
                )
            )
        elif kind == "Error":
            self._fail(f"Deepgram error: {message.get('description') or message.get('message') or message}")

    def _fail(self, reason: str) -> None:
        log.warning("%s track=%s", reason, self._label)
        if self._on_error is not None:
            self._on_error(reason)
