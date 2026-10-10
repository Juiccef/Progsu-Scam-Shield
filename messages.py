"""Bounded message summaries rebuilt from the existing event log. No message bodies."""

from collections import OrderedDict
from pathlib import Path
import json

from history import LOG_TAIL_BYTES


class MessageHistory:
    def __init__(self, limit: int = 50) -> None:
        self.limit = limit
        self._messages: OrderedDict[str, dict] = OrderedDict()

    def record(self, event: dict) -> None:
        if event.get("type") != "message_scored":
            return
        self._messages[event["message_id"]] = dict(event)
        while len(self._messages) > self.limit:
            self._messages.popitem(last=False)

    def contains(self, message_id: str) -> bool:
        return message_id in self._messages

    def list(self) -> list[dict]:
        return list(reversed(self._messages.values()))

    @classmethod
    def from_log(cls, path: Path) -> "MessageHistory":
        history = cls()
        if path.is_file():
            with path.open("rb") as file:
                file.seek(0, 2)
                size = file.tell()
                file.seek(max(0, size - LOG_TAIL_BYTES))
                lines = file.read().decode("utf-8", "replace").splitlines()
            if size > LOG_TAIL_BYTES:
                lines = lines[1:]
            for line in lines:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict):
                    history.record(event)
        return history
