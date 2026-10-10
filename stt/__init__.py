"""Streaming speech-to-text. One stream per audio track (caller, senior)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Protocol


@dataclass(frozen=True)
class STTResult:
    text: str
    is_final: bool  # False: in-progress text that the next result for this track replaces
    start_at: float  # epoch seconds when this stretch of audio started arriving
    end_at: float  # epoch seconds when the last of it arrived


class STTStream(Protocol):
    async def start(self) -> None: ...

    def send_audio(self, chunk: bytes) -> None:
        """Queue raw mu-law 8 kHz audio. Never blocks."""

    async def close(self) -> None: ...


OnResult = Callable[[STTResult], None]
OnError = Callable[[str], None]
# (track label, on_result, on_error) -> stream
STTFactory = Callable[[str, OnResult, OnError], STTStream]


def make_stt_factory(settings) -> Optional[STTFactory]:
    """The configured provider's stream factory, or None when speech-to-text is not set up."""
    if settings.stt_provider == "deepgram" and settings.deepgram_api_key:
        from .deepgram import DeepgramStream

        return lambda label, on_result, on_error: DeepgramStream(
            settings.deepgram_api_key, label, on_result, on_error, model=settings.deepgram_model
        )
    return None
