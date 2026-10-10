"""The Deepgram client against a local stand-in server that speaks Deepgram's streaming protocol."""

import asyncio
import http
import json
import time

from websockets.asyncio.server import serve

import stt.deepgram as deepgram
from stt.deepgram import DeepgramStream

FRAME = b"\xff" * 160  # 20 ms of mu-law silence


def results_message(text, *, final, start=0.0, duration=1.0):
    return json.dumps(
        {
            "type": "Results",
            "start": start,
            "duration": duration,
            "is_final": final,
            "speech_final": final,
            "channel": {"alternatives": [{"transcript": text, "confidence": 0.99}]},
        }
    )


async def until(condition, timeout=3.0):
    deadline = time.time() + timeout
    while not condition() and time.time() < deadline:
        await asyncio.sleep(0.01)
    assert condition(), "timed out waiting for the stream"


def run_against(handler, scenario, monkeypatch, **serve_options):
    async def main():
        async with serve(handler, "127.0.0.1", 0, **serve_options) as server:
            port = server.sockets[0].getsockname()[1]
            monkeypatch.setattr(deepgram, "URL", f"ws://127.0.0.1:{port}/v1/listen")
            return await scenario()

    return asyncio.run(main())


def test_audio_goes_out_and_transcripts_come_back(monkeypatch):
    seen = {"audio": 0, "control": [], "path": "", "auth": ""}

    async def handler(ws):
        seen["path"], seen["auth"] = ws.request.path, ws.request.headers.get("Authorization")
        async for message in ws:
            if isinstance(message, bytes):
                seen["audio"] += len(message)
                if seen["audio"] == 8000:  # one second of audio has arrived
                    await ws.send(results_message("buy gift", final=False))
                    await ws.send(results_message("Buy gift cards.", final=True))
                    await ws.send(json.dumps({"type": "Metadata", "request_id": "abc"}))
            else:
                seen["control"].append(json.loads(message)["type"])
                if seen["control"][-1] == "CloseStream":
                    await ws.close()

    async def scenario():
        results, errors = [], []
        stream = DeepgramStream("dg-key", "inbound", results.append, errors.append)
        await stream.start()
        began = time.time()
        for _ in range(50):
            stream.send_audio(FRAME)
            await asyncio.sleep(0.004)
        await until(lambda: len(results) == 2)
        await stream.close()
        return results, errors, began

    results, errors, began = run_against(handler, scenario, monkeypatch)
    assert errors == []
    assert seen["auth"] == "Token dg-key"
    for parameter in ("model=nova-3", "encoding=mulaw", "sample_rate=8000", "interim_results=true", "endpointing=300"):
        assert parameter in seen["path"]
    assert seen["audio"] == 8000 and seen["control"] == ["CloseStream"]
    interim, final = results
    assert (interim.text, interim.is_final) == ("buy gift", False)
    assert (final.text, final.is_final) == ("Buy gift cards.", True)
    # Audio second 0.0-1.0 of the stream maps back to when those frames reached us.
    assert abs(final.start_at - began) < 0.15
    assert final.start_at < final.end_at <= time.time()


def test_idle_stream_is_kept_alive(monkeypatch):
    monkeypatch.setattr(deepgram, "KEEPALIVE_S", 0.05)
    control = []

    async def handler(ws):
        async for message in ws:
            if isinstance(message, str):
                control.append(json.loads(message)["type"])
                if control[-1] == "CloseStream":
                    await ws.close()

    async def scenario():
        stream = DeepgramStream("dg-key", "outbound", lambda result: None)
        await stream.start()
        await until(lambda: control.count("KeepAlive") >= 2)
        await stream.close()

    run_against(handler, scenario, monkeypatch)
    assert control[-1] == "CloseStream"


def test_rejected_key_is_reported_once_and_not_retried(monkeypatch):
    attempts = []

    def reject(connection, request):
        attempts.append(request.path)
        return connection.respond(http.HTTPStatus.UNAUTHORIZED, "invalid credentials\n")

    async def handler(ws):  # never reached
        await ws.close()

    async def scenario():
        errors = []
        stream = DeepgramStream("bad-key", "inbound", lambda result: None, errors.append)
        await stream.start()
        stream.send_audio(FRAME)
        await until(lambda: errors)
        await asyncio.sleep(0.7)  # longer than the first retry delay
        await stream.close()
        return errors

    errors = run_against(handler, scenario, monkeypatch, process_request=reject)
    assert errors == ["Deepgram refused the connection: HTTP 401 (check DEEPGRAM_API_KEY)"]
    assert len(attempts) == 1


def test_dropped_connection_is_reopened(monkeypatch):
    connections = []

    async def handler(ws):
        connections.append(0)
        index = len(connections) - 1
        async for message in ws:
            if isinstance(message, bytes):
                connections[index] += len(message)
                if index == 0:
                    await ws.close(code=1011)  # server error on the first connection
                    return
                await ws.send(results_message("still listening", final=True, start=0.0, duration=0.02))
            elif json.loads(message)["type"] == "CloseStream":
                await ws.close()

    async def scenario():
        results, errors = [], []
        stream = DeepgramStream("dg-key", "inbound", results.append, errors.append)
        await stream.start()
        stream.send_audio(FRAME)
        await until(lambda: errors)
        for _ in range(100):  # keep talking while it reconnects
            stream.send_audio(FRAME)
            await asyncio.sleep(0.01)
            if results:
                break
        await until(lambda: results)
        await stream.close()
        return results, errors

    results, errors = run_against(handler, scenario, monkeypatch)
    assert len(connections) == 2 and connections[1] > 0
    assert "Deepgram connection closed" in errors[0]
    assert results[0].text == "still listening"


def test_audio_queue_is_bounded_while_disconnected():
    async def scenario():
        stream = DeepgramStream("dg-key", "inbound", lambda result: None)  # never started
        for _ in range(deepgram.MAX_QUEUED_CHUNKS + 25):
            stream.send_audio(FRAME)
        return stream._queue.qsize(), stream.dropped_chunks

    assert asyncio.run(scenario()) == (deepgram.MAX_QUEUED_CHUNKS, 25)
