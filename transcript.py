"""In-memory, speaker-labeled transcript for one call. Nothing here is written to disk."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass
class Segment:
    speaker: str  # CALLER or SENIOR
    text: str
    start: float  # seconds since the call's audio stream started
    end: float
    final: bool = True
    heard_at: float = 0.0  # epoch seconds when the end of this audio reached us
    transcribed_at: float = 0.0  # epoch seconds when its transcript arrived


class Transcript:
    def __init__(self) -> None:
        self._final: list[Segment] = []
        self._interim: dict[str, Segment] = {}

    def add(self, segment: Segment) -> None:
        """Add a finalized segment, or replace the speaker's in-progress (interim) text."""
        if segment.final:
            self._interim.pop(segment.speaker, None)
            if segment.text:
                self._final.append(segment)
        elif segment.text:
            self._interim[segment.speaker] = segment
        else:
            self._interim.pop(segment.speaker, None)

    def window(self, now: float, seconds: float) -> list[Segment]:
        """Segments that overlap the last `seconds`, oldest first, including in-progress text."""
        cutoff = now - seconds
        segments = [segment for segment in self._final if segment.end >= cutoff]
        segments.extend(self._interim.values())
        return sorted(segments, key=lambda segment: segment.start)

    def clear(self) -> None:
        self._final.clear()
        self._interim.clear()

    @staticmethod
    def lines(segments: Iterable[Segment]) -> list[tuple[str, str]]:
        """(speaker, text) lines, merging back-to-back segments from the same speaker."""
        lines: list[tuple[str, str]] = []
        for segment in segments:
            if lines and lines[-1][0] == segment.speaker:
                lines[-1] = (segment.speaker, f"{lines[-1][1]} {segment.text}")
            else:
                lines.append((segment.speaker, segment.text))
        return lines

    @staticmethod
    def render(lines: Iterable[tuple[str, str]]) -> str:
        return "\n".join(f"{speaker}: {text}" for speaker, text in lines)
