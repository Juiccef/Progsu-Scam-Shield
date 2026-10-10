#!/usr/bin/env python3
"""Replay the eval scripts through the same scorer and decision logic used on live calls.

    python eval/run_eval.py --model gpt-4o-mini --prompt prompts/scam-scorer-prompt.md
    python eval/run_eval.py --models rules,claude-haiku-4-5,gpt-4o-mini

Turns are fed cumulatively in timestamp order. Each turn is evaluated when it
arrives, and re-checked every --interval seconds while a threshold is crossed but
not yet confirmed, as on a live call. Time is simulated from the script
timestamps unless --realtime is given. "rules" as a model name runs the regex
rules alone.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import math
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import Settings  # noqa: E402
from scorer.decision import WARN, DecisionConfig, DecisionEngine  # noqa: E402
from scorer.llm import LLMScorer, ScorerPrompt  # noqa: E402
from scorer.pipeline import CallScorer  # noqa: E402
from transcript import Segment, Transcript  # noqa: E402

RULES_ONLY = "rules"
WORDS_PER_SECOND = 2.5  # used only to estimate how long a script's final turn lasts
MAX_RECHECKS = 6


async def run_script(
    script: dict,
    llm: Optional[LLMScorer],
    decision: DecisionConfig,
    window_s: float,
    interval_s: float,
    realtime: bool,
) -> dict:
    scorer = CallScorer(llm, DecisionEngine(decision), window_s=window_s)
    transcript = Transcript()
    turns = script["turns"]
    started = time.monotonic()
    clock = (lambda: time.monotonic() - started) if realtime else None
    result = {
        "id": script["id"],
        "label": script["label"],
        "trigger_turn": script.get("trigger_turn"),
        "warn_turn": None,
        "end_turn": None,
        "detect_turn": None,
        "seconds_from_trigger": None,
        "peak_risk": 0,
        "turns": [],
    }

    async def evaluate(index: int, t: float, recheck: bool) -> None:
        evaluation = await scorer.evaluate(transcript, t, clock=clock)
        result["peak_risk"] = max(result["peak_risk"], evaluation.risk)
        result["turns"].append(
            {
                "turn": index,
                "t": t,
                "speaker": turns[index]["speaker"],
                "recheck": recheck,
                "risk": evaluation.risk,
                "rule_risk": evaluation.rule_risk,
                "llm_risk": evaluation.llm_risk,
                "flags": evaluation.flags,
                "llm_latency_s": evaluation.llm_latency_s,
                "llm_reused": evaluation.llm_reused,
                "llm_error": evaluation.llm_error,
                "actions": [action.kind for action in evaluation.actions],
            }
        )
        for action in evaluation.actions:
            result["warn_turn" if action.kind == WARN else "end_turn"] = index
            if result["detect_turn"] is None:
                result["detect_turn"] = index
                trigger = result["trigger_turn"]
                if trigger is not None:
                    # Script time since the trigger turn began, plus the time the scorer took.
                    result["seconds_from_trigger"] = t - turns[trigger]["t"] + (evaluation.llm_latency_s or 0.0)

    for index, turn in enumerate(turns):
        if realtime:
            await asyncio.sleep(max(0.0, turn["t"] - (time.monotonic() - started)))
        last = index + 1 == len(turns)
        next_t = turn["t"] + len(turn["text"].split()) / WORDS_PER_SECOND if last else turns[index + 1]["t"]
        transcript.add(Segment(turn["speaker"].upper(), turn["text"], float(turn["t"]), float(next_t)))
        await evaluate(index, float(turn["t"]), recheck=False)

        # Live calls look again every interval while a threshold is crossed but unconfirmed,
        # even if nobody speaks. Do the same until the next turn (or a few times after the last).
        recheck_t = turn["t"] + interval_s
        for _ in range(MAX_RECHECKS):
            if not scorer.engine.awaiting_confirmation or (not last and recheck_t >= next_t):
                break
            if realtime:
                await asyncio.sleep(max(0.0, recheck_t - (time.monotonic() - started)))
            await evaluate(index, float(recheck_t), recheck=True)
            recheck_t += interval_s
        if scorer.engine.ended:
            break
    return result


async def run_model(model: str, scripts: list[dict], args: argparse.Namespace, decision: DecisionConfig) -> dict:
    llm = None
    if model != RULES_ONLY:
        llm = LLMScorer(
            model, ScorerPrompt.load(args.prompt), timeout_s=args.timeout, early_exit=not args.full_reply
        )
    limit = asyncio.Semaphore(len(scripts) if args.realtime else args.concurrency)

    async def bounded(script: dict) -> dict:
        async with limit:
            return await run_script(script, llm, decision, args.window, args.interval, args.realtime)

    try:
        results = await asyncio.gather(*(bounded(script) for script in scripts))
    finally:
        if llm is not None:
            await llm.aclose()
    return {"summary": summarize(results), "scripts": results}


def summarize(results: list[dict]) -> dict:
    scams = [r for r in results if r["label"] == "scam"]
    legit = [r for r in results if r["label"] != "scam"]
    detected = [r for r in scams if r["detect_turn"] is not None]
    calls = [turn for r in results for turn in r["turns"] if turn["llm_latency_s"] is not None]
    answered = sorted(turn["llm_latency_s"] for turn in calls if turn["llm_error"] is None)
    errors = [turn["llm_error"] for turn in calls if turn["llm_error"]]
    return {
        "scam_scripts": len(scams),
        "legit_scripts": len(legit),
        "scams_detected": len(detected),
        "scams_ended": sum(r["end_turn"] is not None for r in scams),
        "false_alarms": sum(r["detect_turn"] is not None for r in legit),
        "false_ends": sum(r["end_turn"] is not None for r in legit),
        "median_turns_to_detect": _median([r["detect_turn"] + 1 for r in detected]),
        "median_turns_after_trigger": _median([r["detect_turn"] - r["trigger_turn"] for r in detected]),
        "median_seconds_from_trigger": _median([r["seconds_from_trigger"] for r in detected]),
        "llm_calls": len(calls),
        "llm_fallbacks": len(errors),
        "llm_p50_s": _percentile(answered, 0.50),
        "llm_p95_s": _percentile(answered, 0.95),
        "llm_first_error": errors[0] if errors else None,
    }


def _median(values: list) -> Optional[float]:
    return statistics.median(values) if values else None


def _percentile(ordered: list[float], fraction: float) -> Optional[float]:
    if not ordered:
        return None
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def _rate(count: int, total: int) -> str:
    return f"{count}/{total} ({round(100 * count / total)}%)" if total else "n/a"


def _number(value: Optional[float], pattern: str = "{:g}") -> str:
    return "n/a" if value is None else pattern.format(value)


def summary_table(models: dict[str, dict]) -> str:
    rows = [
        "| Model | Scams detected | Calls ended | False alarms | Turns to detect (median) "
        "| Seconds from trigger (median) | LLM p50 / p95 | LLM fallbacks |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for model, run in models.items():
        s = run["summary"]
        if s["llm_calls"]:
            latency = f"{_number(s['llm_p50_s'], '{:.2f}')} / {_number(s['llm_p95_s'], '{:.2f}')} s"
            fallbacks = _rate(s["llm_fallbacks"], s["llm_calls"])
        else:
            latency = fallbacks = "n/a"
        rows.append(
            f"| {model} | {_rate(s['scams_detected'], s['scam_scripts'])} | {_rate(s['scams_ended'], s['scam_scripts'])} "
            f"| {_rate(s['false_alarms'], s['legit_scripts'])} "
            f"| {_number(s['median_turns_to_detect'])} ({_number(s['median_turns_after_trigger'], '{:+g}')} vs trigger) "
            f"| {_number(s['median_seconds_from_trigger'], '{:+.1f}')} | {latency} | {fallbacks} |"
        )
    return "\n".join(rows)


def script_table(run: dict) -> str:
    rows = [
        "| Script | Trigger turn | WARN turn | END turn | Peak risk | Risk by turn (re-checks in brackets) |",
        "|---|---|---|---|---|---|",
    ]
    for r in run["scripts"]:
        cells = [r["id"], r["trigger_turn"], r["warn_turn"], r["end_turn"], r["peak_risk"]]
        risks = " ".join(f"({turn['risk']})" if turn["recheck"] else str(turn["risk"]) for turn in r["turns"])
        rows.append("| " + " | ".join("-" if cell is None else str(cell) for cell in cells) + f" | {risks} |")
    return "\n".join(rows)


def parse_args(settings: Settings) -> argparse.Namespace:
    decision = settings.decision
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", help=f"model spec, or 'rules' (default: {settings.scorer_model})")
    parser.add_argument("--models", help="comma-separated model specs to compare")
    parser.add_argument("--prompt", default=str(settings.scorer_prompt))
    parser.add_argument("--scripts", default=str(ROOT / "eval" / "eval-scripts.json"))
    parser.add_argument("--only", help="run only scripts whose id contains this text")
    parser.add_argument("--realtime", action="store_true", help="wait for each turn's timestamp instead of simulating time")
    parser.add_argument("--concurrency", type=int, default=4, help="scripts scored in parallel (simulated time)")
    parser.add_argument("--timeout", type=float, default=settings.llm_timeout_s, help="LLM timeout in seconds")
    parser.add_argument(
        "--full-reply",
        action="store_true",
        default=not settings.llm_early_exit,
        help="read the model's whole reply instead of stopping once risk and flags have arrived",
    )
    parser.add_argument("--window", type=float, default=settings.window_s, help="transcript window in seconds")
    parser.add_argument("--interval", type=float, default=settings.score_interval_s, help="seconds between re-checks")
    parser.add_argument("--warn", type=int, default=decision.warn_threshold)
    parser.add_argument("--end", type=int, default=decision.end_threshold)
    parser.add_argument("--consecutive", type=int, default=decision.consecutive_required)
    parser.add_argument("--grace", type=float, default=decision.grace_period_s)
    parser.add_argument("--out", default=str(ROOT / "eval" / "results"))
    return parser.parse_args()


async def main() -> int:
    settings = Settings.from_env()
    args = parse_args(settings)
    decision = dataclasses.replace(
        settings.decision,
        warn_threshold=args.warn,
        end_threshold=args.end,
        consecutive_required=args.consecutive,
        grace_period_s=args.grace,
    )
    scripts = json.loads(Path(args.scripts).read_text(encoding="utf-8"))["scripts"]
    if args.only:
        scripts = [script for script in scripts if args.only in script["id"]]
    model_specs = [m.strip() for m in (args.models or args.model or settings.scorer_model).split(",") if m.strip()]

    models: dict[str, dict] = {}
    for model in model_specs:
        print(f"Running {len(scripts)} scripts with {model} ...", file=sys.stderr)
        try:
            models[model] = await run_model(model, scripts, args, decision)
        except ValueError as exc:  # unknown provider or missing API key
            print(f"Skipping {model}: {exc}", file=sys.stderr)
    if not models:
        return 1

    print(summary_table(models))
    for model, run in models.items():
        print(f"\n### {model}\n")
        print(script_table(run))
        if run["summary"]["llm_first_error"]:
            print(f"\nFirst LLM error: {run['summary']['llm_first_error']}")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    out_file.write_text(
        json.dumps(
            {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "scripts_file": str(args.scripts),
                "prompt_file": str(args.prompt),
                "realtime": args.realtime,
                "window_s": args.window,
                "score_interval_s": args.interval,
                "llm_timeout_s": args.timeout,
                "decision": dataclasses.asdict(decision),
                "models": models,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nWrote {out_file.relative_to(ROOT) if out_file.is_relative_to(ROOT) else out_file}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
