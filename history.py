"""Summaries of recent calls, for an app's home screen and its "what happened" view.

A summary is built only from events that are allowed on disk, so it never holds
transcript text: it records what was detected, not what was said.
"""

from __future__ import annotations

import json
from collections import OrderedDict
from pathlib import Path

MAX_CALLS = 50
MAX_TIMELINE_POINTS = 240
LOG_TAIL_BYTES = 2_000_000  # enough for far more than MAX_CALLS calls


class CallHistory:
    def __init__(self, limit: int = MAX_CALLS) -> None:
        self._limit = limit
        self._calls: OrderedDict[str, dict] = OrderedDict()  # oldest first

    def record(self, event: dict) -> None:
        """Fold one published event into its call's summary."""
        kind, call_sid = event.get("type"), event.get("call_sid")
        if not call_sid:
            return
        if kind == "call_started":
            if call_sid not in self._calls:
                self._calls[call_sid] = {
                    "call_sid": call_sid,
                    "started_at": event.get("ts"),
                    "caller": event.get("caller", "unknown"),
                    "monitored": bool(event.get("monitored", True)),
                    "simulated": bool(event.get("simulated", False)),
                    "answered_at": None,
                    "ended_at": None,
                    "outcome": "in_progress",
                    "duration_s": None,
                    "peak_risk": 0,
                    "flags": [],
                    "actions": [],
                    "timeline": [],
                }
                while len(self._calls) > self._limit:
                    self._calls.popitem(last=False)
            return
        call = self._calls.get(call_sid)
        if call is None:
            return
        if kind == "call_answered":
            call["answered_at"] = event.get("ts")
        elif kind in ("score", "rule_hit"):
            call["flags"].extend(flag for flag in event.get("flags") or [] if flag not in call["flags"])
            if kind == "score":
                risk = int(event.get("risk") or 0)
                call["peak_risk"] = max(call["peak_risk"], risk)
                if len(call["timeline"]) < MAX_TIMELINE_POINTS:
                    call["timeline"].append([event.get("t", 0), risk])
        elif kind == "action":
            call["actions"].append(
                {
                    "kind": event.get("kind"),
                    "risk": event.get("risk"),
                    "reason": event.get("reason", ""),
                    "at": event.get("ts"),
                    "seconds": None,
                }
            )
        elif kind == "action_result":
            for action in reversed(call["actions"]):
                if action["kind"] == event.get("kind"):
                    action["seconds"] = event.get("seconds")
                    break
        elif kind == "call_ended":
            call["outcome"] = event.get("outcome", "completed")
            call["duration_s"] = event.get("duration_s")
            call["ended_at"] = event.get("ts")

    def list(self) -> list[dict]:
        """Newest call first."""
        return list(reversed(self._calls.values()))

    @classmethod
    def from_log(cls, path: Path, limit: int = MAX_CALLS) -> "CallHistory":
        """Rebuild from the tail of events.jsonl, so history survives a restart."""
        history = cls(limit)
        if not path.is_file():
            return history
        with open(path, "rb") as log_file:
            log_file.seek(0, 2)
            size = log_file.tell()
            log_file.seek(max(0, size - LOG_TAIL_BYTES))
            lines = log_file.read().decode("utf-8", "replace").splitlines()
        if size > LOG_TAIL_BYTES:
            lines = lines[1:]  # the first line is probably cut in half
        last_ts = None
        for line in lines:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                history.record(event)
                last_ts = event.get("ts", last_ts)
        for call in history._calls.values():
            if call["outcome"] == "in_progress":  # the server stopped mid-call
                kinds = {action["kind"] for action in call["actions"]}
                call["outcome"] = "ended_by_shield" if "END" in kinds else "warned" if "WARN" in kinds else "completed"
                call["ended_at"] = last_ts
        return history
