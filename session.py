"""One live call: transcripts in, evaluations and actions out.

A session does not care where its transcript comes from, so tests and tools can drive
it without Twilio or a speech-to-text provider.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections import deque
from contextlib import suppress
from typing import Callable, Optional

from actions import Actions, reason_for
from config import Settings
from events import EventBus, mask_phone
from scorer.decision import END, Action, DecisionEngine
from scorer.flags import CALLER, SENIOR
from scorer.llm import LLMScorer
from scorer.pipeline import CallScorer, Evaluation
from scorer.rules import highlights
from transcript import Segment, Transcript

log = logging.getLogger("shield.call")

STALE_AFTER_S = 6 * 3600


class CallSession:
    def __init__(
        self,
        call_sid: str,
        *,
        settings: Settings,
        bus: EventBus,
        actions: Actions,
        llm: Optional[LLMScorer],
        caller: str = "",
        monitored: bool = True,
        simulated: bool = False,
        on_action: Optional[Callable[[dict], None]] = None,
    ) -> None:
        self.call_sid = call_sid
        self.caller = mask_phone(caller)
        self.monitored = monitored
        self.simulated = simulated  # a demo call: no phone, no Twilio
        self.token = secrets.token_urlsafe(16)  # proves a media stream was started by our own TwiML
        self.child_sid: Optional[str] = None  # the senior's call leg
        self.created_at = time.time()
        self.stream_started_at: Optional[float] = None
        self.answered_at: Optional[float] = None
        self.outcome = "completed"
        self.settings = settings
        self.bus = bus
        self.actions = actions
        self.transcript = Transcript()
        self.scorer = CallScorer(
            llm, DecisionEngine(settings.decision, monitoring_enabled=monitored), window_s=settings.window_s
        )
        self._llm = llm
        self._on_action = on_action
        self._evaluations: deque[tuple[Evaluation, float]] = deque(maxlen=64)  # (evaluation, scored at)
        self._evaluation_count = 0
        self._rule_flags_seen: set[str] = set()
        self._dirty = False  # there is text that has not been scored
        self._urgent = False  # score it now instead of at the next interval
        self._wake = asyncio.Event()
        self._last_evaluation_started = 0.0
        self._loop_task: Optional[asyncio.Task] = None
        self._warm_up_task: Optional[asyncio.Task] = None
        self._action_task: Optional[asyncio.Task] = None
        self._closed = False

    @property
    def streaming(self) -> bool:
        return self.stream_started_at is not None and not self._closed

    def begin_stream(self) -> None:
        """The audio stream is up: start the clock and the scoring loop."""
        if self._loop_task is not None:
            return
        self.stream_started_at = time.time()
        self._loop_task = asyncio.create_task(self._score_loop(), name=f"score-{self.call_sid}")
        if self._llm is not None:
            # Opens the connection to the model while the senior's phone is still ringing.
            self._warm_up_task = asyncio.create_task(self._llm.warm_up())

    def mark_answered(self) -> None:
        """The senior picked up. The no-action grace period counts from here."""
        if self.answered_at is None:
            self.answered_at = time.time()
            self.bus.publish("call_answered", call_sid=self.call_sid)

    def add_transcript(self, speaker: str, text: str, *, final: bool, start_at: float, end_at: float) -> None:
        """Feed finalized or in-progress text. `start_at`/`end_at` are when that audio reached us (epoch seconds)."""
        if self._closed or self.stream_started_at is None:
            return
        t0 = self.stream_started_at
        self.transcript.add(
            Segment(speaker, text, start_at - t0, end_at - t0, final, heard_at=end_at, transcribed_at=time.time())
        )
        if not text:
            return
        if speaker == SENIOR and self.answered_at is None:
            self.answered_at = start_at  # no status callback yet, but the senior is clearly on the line
        # The caller's words that matched a rule, so a client can mark them in the transcript.
        # They are transcript text: this event is memory-only and never reaches the disk.
        flagged = highlights(text) if speaker == CALLER else []
        self.bus.publish(
            "transcript",
            call_sid=self.call_sid,
            speaker=speaker,
            text=text,
            final=final,
            start=round(start_at - t0, 2),
            end=round(end_at - t0, 2),
            **({"highlights": flagged} if flagged else {}),
        )
        if final and self.settings.log_transcripts:
            log.info("call=%s %s: %s", self.call_sid, speaker, text)
        if self.scorer.engine.ended:
            return
        self._dirty = True
        if speaker == CALLER:
            # Rules are cheap enough to run on every partial transcript. A new flag means the
            # LLM should look now rather than at the end of the sentence.
            rule = self.scorer.rules(self.transcript, self._stream_time())
            new_flags = set(rule.flags) - self._rule_flags_seen
            if new_flags:
                self._rule_flags_seen |= new_flags
                self.bus.publish("rule_hit", call_sid=self.call_sid, flags=sorted(new_flags), rule_risk=rule.risk)
            if final or new_flags:
                self._urgent = True
        self._wake.set()

    def _stream_time(self) -> float:
        return time.time() - (self.stream_started_at or time.time())

    def _call_time(self) -> float:
        """Seconds since the senior answered (or since the stream started, if we never learned that)."""
        return time.time() - (self.answered_at or self.stream_started_at or time.time())

    async def _score_loop(self) -> None:
        while not self._closed and not self.scorer.engine.ended:
            await self._wake.wait()
            self._wake.clear()
            while self._dirty and not self._closed and not self.scorer.engine.ended:
                gap = self.settings.score_min_gap_s if self._urgent else self.settings.score_interval_s
                wait = self._last_evaluation_started + gap - time.time()
                if wait > 0:
                    # Sleep until the next evaluation is due, or until something urgent arrives.
                    with suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(self._wake.wait(), timeout=wait)
                    self._wake.clear()
                    continue
                self._dirty = self._urgent = False
                self._last_evaluation_started = time.time()
                try:
                    await self._evaluate()
                except Exception:
                    log.exception("Evaluation failed on call %s", self.call_sid)
                if self.scorer.engine.awaiting_confirmation:
                    # Risk is over a threshold but not yet confirmed. Look again after the
                    # interval even if nobody speaks, so a pause cannot stall the action.
                    self._dirty = True

    async def _evaluate(self) -> None:
        evaluation = await self.scorer.evaluate(self.transcript, self._stream_time(), clock=self._call_time)
        scored_at = time.time()
        self._evaluations.append((evaluation, scored_at))
        self._evaluation_count += 1
        latency_ms = None if evaluation.llm_latency_s is None else round(evaluation.llm_latency_s * 1000)
        self.bus.publish(
            "score",
            call_sid=self.call_sid,
            t=round(evaluation.t, 1),
            risk=evaluation.risk,
            rule_risk=evaluation.rule_risk,
            llm_risk=evaluation.llm_risk,
            label=evaluation.label,
            flags=evaluation.flags,
            llm_latency_ms=latency_ms,
            llm_reused=evaluation.llm_reused,
            llm_error=evaluation.llm_error,
            explanation=evaluation.explanation,
            evidence=evaluation.evidence,
        )
        log.info(
            "score call=%s t=%.1fs risk=%d (rules=%d llm=%s%s) flags=%s",
            self.call_sid,
            evaluation.t,
            evaluation.risk,
            evaluation.rule_risk,
            evaluation.llm_risk,
            f" {latency_ms}ms" if latency_ms is not None else "",
            ",".join(evaluation.flags) or "-",
        )
        if evaluation.actions:
            # Shielded: the call dropping (which END causes) must not cancel the SMS or the metrics.
            self._action_task = asyncio.create_task(self._act(evaluation, scored_at))
            await asyncio.shield(self._action_task)

    async def _act(self, evaluation: Evaluation, decided_at: float) -> None:
        for action in evaluation.actions:
            reason = reason_for(evaluation.flags)
            self.outcome = "ended_by_shield" if action.kind == END else "warned"
            log.warning("%s call=%s risk=%d: %s", action.kind, self.call_sid, action.risk, reason)
            # The published event is the alert itself; it does not wait on Twilio.
            self.bus.publish(
                "action", call_sid=self.call_sid, kind=action.kind, risk=action.risk, flags=evaluation.flags, reason=reason
            )
            if action.kind == END:
                result = await self.actions.end(self.call_sid, self.child_sid, reason)
                sent_at = result.pop("call_control_at")
            else:
                sent_at = time.time()
                result = await self.actions.warn(self.call_sid, reason)
            metrics = self._metrics(action, evaluation, decided_at, sent_at)
            if self._on_action is not None:
                self._on_action(metrics)
            self.bus.publish("action_result", **result, **metrics)
            log.info(
                "%s done call=%s %s seconds_from_utterance=%s",
                action.kind,
                self.call_sid,
                " ".join(f"{key}={value}" for key, value in result.items()),
                metrics["seconds"],
            )

    def _metrics(self, action: Action, evaluation: Evaluation, decided_at: float, sent_at: float) -> dict:
        """Stage timestamps from the utterance that started the streak to the action going out."""
        trigger, first_scored_at = next(
            ((e, at) for e, at in self._evaluations if e.t == action.streak_started_t), (evaluation, decided_at)
        )
        heard_at = trigger.heard_at or None
        transcribed_at = trigger.transcribed_at or None

        def span(start: Optional[float], end: Optional[float]) -> Optional[float]:
            return None if start is None or end is None else round(max(0.0, end - start), 3)

        return {
            "call_sid": self.call_sid,
            "kind": action.kind,
            "risk": action.risk,
            "seconds": span(heard_at, sent_at),  # triggering utterance heard -> action sent
            "stages_s": {
                "speech_to_text": span(heard_at, transcribed_at),
                "scoring": span(transcribed_at, first_scored_at),
                "confirmation": span(first_scored_at, decided_at),
                "action": span(decided_at, sent_at),
            },
            "utterance_heard_at": heard_at,
            "transcribed_at": transcribed_at,
            "first_scored_at": first_scored_at,
            "decided_at": decided_at,
            "action_sent_at": sent_at,
        }

    async def close(self, reason: str = "call ended") -> None:
        if self._closed:
            return
        self._closed = True
        self._wake.set()
        for task in (self._loop_task, self._warm_up_task):
            if task is not None and not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await task
        if self._action_task is not None and not self._action_task.done():
            await asyncio.wait({self._action_task}, timeout=15)
        self.transcript.clear()
        duration = round(time.time() - (self.answered_at or self.created_at), 1)
        self.bus.publish(
            "call_ended",
            call_sid=self.call_sid,
            outcome=self.outcome,
            reason=reason,
            duration_s=duration,
            evaluations=self._evaluation_count,
        )
        self.bus.forget_call(self.call_sid)
        log.info("call ended call=%s outcome=%s (%s) evaluations=%d", self.call_sid, self.outcome, reason, self._evaluation_count)


class CallRegistry:
    """Calls in progress, keyed by the caller leg's CallSid."""

    def __init__(self, factory: Callable[[str, str, bool, bool], CallSession]) -> None:
        self._factory = factory  # (call_sid, caller, monitored, simulated) -> session
        self._calls: dict[str, CallSession] = {}

    def __len__(self) -> int:
        return len(self._calls)

    def open(self, call_sid: str, caller: str, monitored: bool, simulated: bool = False) -> CallSession:
        cutoff = time.time() - STALE_AFTER_S
        for stale in [sid for sid, call in self._calls.items() if call.created_at < cutoff and not call.streaming]:
            del self._calls[stale]
        if call_sid not in self._calls:
            self._calls[call_sid] = self._factory(call_sid, caller, monitored, simulated)
        return self._calls[call_sid]

    def get(self, call_sid: str) -> Optional[CallSession]:
        return self._calls.get(call_sid)

    def any_simulated(self) -> bool:
        return any(call.simulated for call in self._calls.values())

    async def close(self, call_sid: str, reason: str) -> None:
        session = self._calls.pop(call_sid, None)
        if session is not None:
            await session.close(reason)

    async def close_all(self) -> None:
        for call_sid in list(self._calls):
            await self.close(call_sid, "server shutting down")
