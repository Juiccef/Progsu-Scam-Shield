"""Decision engine: turns a stream of risk evaluations into at most one WARN and one END.

risk = max(llm_risk, rule_risk). An action needs `consecutive_required`
confirmations in a row at or above its threshold. One evaluation normally counts
as one confirmation; when the rules and the LLM are both over the threshold in
the same evaluation it counts as two, because two independent scorers agree
(set `agreement_confirms=False` to require separate evaluations instead).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional

WARN = "WARN"
END = "END"


@dataclass(frozen=True)
class DecisionConfig:
    warn_threshold: int = 60
    end_threshold: int = 85
    consecutive_required: int = 2
    grace_period_s: float = 10.0
    agreement_confirms: bool = True


@dataclass(frozen=True)
class Action:
    kind: str  # WARN or END
    risk: int
    t: float  # seconds into the call when the action fired
    streak_started_t: float  # time of the first evaluation in the streak that led to it


def combine_risk(rule_risk: int, llm_risk: Optional[int]) -> int:
    return max(rule_risk, llm_risk if llm_risk is not None else 0)


class _Level:
    def __init__(self, kind: str, threshold: int) -> None:
        self.kind = kind
        self.threshold = threshold
        self.streak = 0
        self.started_t = 0.0
        self.fired = False


class DecisionEngine:
    """Feed it one evaluation at a time; it returns the actions to take now."""

    def __init__(self, config: DecisionConfig = DecisionConfig(), *, monitoring_enabled: bool = True) -> None:
        self.config = config
        self.monitoring_enabled = monitoring_enabled
        self._warn = _Level(WARN, config.warn_threshold)
        self._end = _Level(END, config.end_threshold)

    @property
    def ended(self) -> bool:
        return self._end.fired

    @property
    def awaiting_confirmation(self) -> bool:
        """An action that has not fired yet has a streak going, so a follow-up evaluation is worth running."""
        return self.monitoring_enabled and any(
            level.streak > 0 and not level.fired for level in (self._warn, self._end)
        )

    def observe(self, t: float, rule_risk: int, llm_risk: Optional[int] = None) -> list[Action]:
        if not self.monitoring_enabled:
            return []
        risk = combine_risk(rule_risk, llm_risk)
        fired: list[Action] = []
        for level in (self._end, self._warn):
            confirmations = self._confirmations(level.threshold, rule_risk, llm_risk)
            if confirmations == 0:
                level.streak = 0
                continue
            if level.streak == 0:
                level.started_t = t
            level.streak += confirmations
            if level.fired or level.streak < self.config.consecutive_required or t < self.config.grace_period_s:
                continue
            level.fired = True
            fired.append(Action(level.kind, risk, t, level.started_t))
        if self._end.fired:
            # END already notifies the caregiver, so a WARN at the same moment or later is noise.
            self._warn.fired = True
            fired = [action for action in fired if action.kind == END]
        return fired

    def _confirmations(self, threshold: int, rule_risk: int, llm_risk: Optional[int]) -> int:
        over = [risk for risk in (rule_risk, llm_risk) if risk is not None and risk >= threshold]
        if not over:
            return 0
        return 2 if self.config.agreement_confirms and len(over) == 2 else 1


_NON_DIGITS = re.compile(r"\D+")


def normalize_phone(number: str) -> str:
    """Digits only, with a leading 1 added to 10-digit North American numbers."""
    digits = _NON_DIGITS.sub("", number or "")
    return "1" + digits if len(digits) == 10 else digits


def is_allowlisted(number: str, allowlist: Iterable[str]) -> bool:
    """True when the caller's number is on the allowlist. Withheld numbers never match."""
    target = normalize_phone(number)
    if len(target) < 7:
        return False
    return any(target == normalize_phone(entry) for entry in allowlist)
