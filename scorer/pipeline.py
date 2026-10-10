"""One scoring pass, shared by live calls and the eval harness.

window of transcript -> rules + LLM -> decision engine -> actions
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from transcript import Transcript

from .decision import Action, DecisionEngine, combine_risk
from .flags import CALLER, label_for
from .llm import LLMScorer
from .rules import RuleResult, score_lines


@dataclass
class Evaluation:
    t: float  # seconds into the call when the decision was made
    rule_risk: int
    llm_risk: Optional[int]
    risk: int
    label: str
    flags: list[str]
    explanation: str = ""
    evidence: list[str] = field(default_factory=list)
    llm_latency_s: Optional[float] = None  # None when the LLM was not called for this evaluation
    llm_error: Optional[str] = None  # why there is no LLM result, when one was expected
    llm_reused: bool = False  # nothing new was said, so the LLM's previous answer stands
    actions: list[Action] = field(default_factory=list)
    heard_at: float = 0.0  # epoch seconds: the caller's newest audio covered by this evaluation
    transcribed_at: float = 0.0  # epoch seconds: when that audio's transcript arrived


class CallScorer:
    """Per-call scoring state: the LLM's previous risk and the decision engine."""

    def __init__(self, llm: Optional[LLMScorer], engine: DecisionEngine, *, window_s: float = 40.0) -> None:
        self.llm = llm
        self.engine = engine
        self.window_s = window_s
        self.previous_llm_risk = 0
        self._last_text = ""
        self._last_outcome = None

    def rules(self, transcript: Transcript, now: float) -> RuleResult:
        """Rules only. Cheap enough to run on every partial transcript."""
        return score_lines(Transcript.lines(transcript.window(now, self.window_s)))

    async def evaluate(
        self, transcript: Transcript, now: float, *, clock: Optional[Callable[[], float]] = None
    ) -> Evaluation:
        """Score the last `window_s` seconds. `clock` gives the decision time if it differs from `now`."""
        segments = transcript.window(now, self.window_s)
        lines = Transcript.lines(segments)
        rule = score_lines(lines)

        outcome, reused = None, False
        if self.llm is not None and any(speaker == CALLER for speaker, _ in lines):
            text = Transcript.render(lines)
            if text == self._last_text and self._last_outcome is not None and self._last_outcome.result is not None:
                # A re-check with nothing new said. Asking again cannot add information, and
                # feeding the model its own previous risk tends to nudge the number upward.
                outcome, reused = self._last_outcome, True
            else:
                outcome = await self.llm.score(text, self.previous_llm_risk)
                self._last_text, self._last_outcome = text, outcome
        llm = outcome.result if outcome else None
        if llm is not None:
            self.previous_llm_risk = llm.risk

        risk = combine_risk(rule.risk, llm.risk if llm else None)
        t = clock() if clock else now
        actions = self.engine.observe(t, rule.risk, llm.risk if llm else None)
        # The caller's most recent speech is "the triggering utterance" for latency metrics.
        newest = max(
            (segment for segment in segments if segment.speaker == CALLER),
            key=lambda segment: segment.heard_at,
            default=None,
        )
        return Evaluation(
            t=t,
            rule_risk=rule.risk,
            llm_risk=llm.risk if llm else None,
            risk=risk,
            label=label_for(risk),
            flags=list(dict.fromkeys((*(llm.flags if llm else ()), *rule.flags))),
            explanation=llm.explanation if llm else "",
            evidence=list(llm.evidence) if llm else [],
            llm_latency_s=outcome.latency_s if outcome and not reused else None,
            llm_error=outcome.error if outcome else None,
            llm_reused=reused,
            actions=actions,
            heard_at=newest.heard_at if newest else 0.0,
            transcribed_at=newest.transcribed_at if newest else 0.0,
        )
