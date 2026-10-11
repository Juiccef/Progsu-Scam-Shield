#!/usr/bin/env python3
"""Score the text-message eval set the way the server scores a text sent to the proxy number.

    python eval/run_text_eval.py --models rules,claude-haiku-4-5,jev-latest
    python eval/run_text_eval.py --model jev-latest --only scam_01

Each text goes through scorer/messages.py with the prompt for texts, as on the server. A text
is held for the caregiver when its risk is 30 or more, or when a model is set and did not
answer ("could not check"), as gateway.py does. "Model alone" is the model's own risk with the
rules left out: the rules are the same for every model, so that is where models differ.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import Settings  # noqa: E402
from run_eval import RULES_ONLY, _number, _percentile, _rate  # noqa: E402
from scorer.llm import LLMScorer, ScorerPrompt  # noqa: E402
from scorer.messages import MessageScorer  # noqa: E402

HOLD_AT = 30  # gateway.py holds a text at this risk or more
LIKELY_SCAM_AT = 70  # the "Likely scam" label


async def run_model(model: str, texts: list[dict], args: argparse.Namespace) -> dict:
    llm: Optional[LLMScorer] = None
    if model != RULES_ONLY:
        # As main.py builds the scorer for texts: its own prompt, and the whole reply is read.
        llm = LLMScorer(model, ScorerPrompt.load(args.prompt), timeout_s=args.timeout, early_exit=False)
    scorer = MessageScorer(llm)
    limit = asyncio.Semaphore(args.concurrency)

    async def one(item: dict) -> dict:
        async with limit:
            started = time.perf_counter()
            result = await scorer.evaluate(item["text"])
            seconds = time.perf_counter() - started
        failed = llm is not None and result["llm_error"] is not None
        return {
            "id": item["id"],
            "label": item["label"],
            "hard": bool(item.get("hard")),
            "risk": result["risk"],
            "rule_risk": result["rule_risk"],
            "llm_risk": result["llm_risk"],
            "flags": result["flags"],
            "llm_error": result["llm_error"],
            "held": failed or result["risk"] >= HOLD_AT,
            "llm_latency_s": seconds if llm is not None else None,
        }

    try:
        results = await asyncio.gather(*(one(item) for item in texts))
    finally:
        if llm is not None:
            await llm.aclose()
    return {"summary": summarize(results), "texts": results}


def summarize(results: list[dict]) -> dict:
    scams = [r for r in results if r["label"] == "scam"]
    legit = [r for r in results if r["label"] != "scam"]
    asked = [r for r in results if r["llm_latency_s"] is not None]
    answered = sorted(r["llm_latency_s"] for r in asked if r["llm_error"] is None)
    errors = [r["llm_error"] for r in asked if r["llm_error"]]

    def model_alone(group: list[dict]) -> Optional[int]:
        return sum((r["llm_risk"] or 0) >= HOLD_AT for r in group) if asked else None

    return {
        "scam_texts": len(scams),
        "legit_texts": len(legit),
        "scams_held": sum(r["held"] for r in scams),
        "legit_held": sum(r["held"] for r in legit),
        "scams_likely": sum(r["risk"] >= LIKELY_SCAM_AT for r in scams),
        "model_alone_scams": model_alone(scams),
        "model_alone_legit": model_alone(legit),
        "llm_calls": len(asked),
        "llm_fallbacks": len(errors),
        "llm_p50_s": _percentile(answered, 0.50),
        "llm_p95_s": _percentile(answered, 0.95),
        "llm_first_error": errors[0] if errors else None,
    }


def summary_table(models: dict[str, dict]) -> str:
    rows = [
        "| Model | Scams held | Ordinary texts held | Scams called likely scam | Model alone: scams over 30 "
        "| Model alone: ordinary over 30 | Could not check | Model p50 / p95 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for model, run in models.items():
        s = run["summary"]
        if s["llm_calls"]:
            alone_scams = _rate(s["model_alone_scams"], s["scam_texts"])
            alone_legit = _rate(s["model_alone_legit"], s["legit_texts"])
            failed = _rate(s["llm_fallbacks"], s["llm_calls"])
            latency = f"{_number(s['llm_p50_s'], '{:.2f}')} / {_number(s['llm_p95_s'], '{:.2f}')} s"
        else:
            alone_scams = alone_legit = failed = latency = "n/a"
        rows.append(
            f"| {model} | {_rate(s['scams_held'], s['scam_texts'])} | {_rate(s['legit_held'], s['legit_texts'])} "
            f"| {_rate(s['scams_likely'], s['scam_texts'])} | {alone_scams} | {alone_legit} | {failed} | {latency} |"
        )
    return "\n".join(rows)


def text_table(run: dict) -> str:
    rows = ["| Text | Rules | Model | Risk | Outcome | Flags |", "|---|---|---|---|---|---|"]
    for r in run["texts"]:
        right = r["held"] == (r["label"] == "scam")
        outcome = ("held" if r["held"] else "forwarded") + ("" if right else " (wrong)")
        if r["llm_error"]:
            outcome += ", could not check"
        model = "-" if r["llm_risk"] is None else str(r["llm_risk"])
        name = r["id"] + (" (hard)" if r["hard"] else "")
        rows.append(f"| {name} | {r['rule_risk']} | {model} | {r['risk']} | {outcome} | {', '.join(r['flags'][:4])} |")
    return "\n".join(rows)


def parse_args(settings: Settings) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", help=f"model spec, or 'rules' (default: {settings.scorer_model})")
    parser.add_argument("--models", help="comma-separated model specs to compare")
    parser.add_argument("--prompt", default=str(ROOT / "prompts" / "message-scorer-prompt.md"))
    parser.add_argument("--texts", default=str(ROOT / "eval" / "text-scripts.json"))
    parser.add_argument("--only", help="run only texts whose id contains this text")
    parser.add_argument("--concurrency", type=int, default=4, help="texts scored in parallel")
    parser.add_argument("--timeout", type=float, default=settings.llm_timeout_s, help="model timeout in seconds")
    parser.add_argument("--out", default=str(ROOT / "eval" / "results"))
    return parser.parse_args()


async def main() -> int:
    settings = Settings.from_env()
    args = parse_args(settings)
    texts = json.loads(Path(args.texts).read_text(encoding="utf-8"))["texts"]
    if args.only:
        texts = [item for item in texts if args.only in item["id"]]
    model_specs = [m.strip() for m in (args.models or args.model or settings.scorer_model).split(",") if m.strip()]

    models: dict[str, dict] = {}
    for model in model_specs:
        print(f"Scoring {len(texts)} texts with {model} ...", file=sys.stderr)
        try:
            models[model] = await run_model(model, texts, args)
        except ValueError as exc:  # unknown provider or missing API key
            print(f"Skipping {model}: {exc}", file=sys.stderr)
    if not models:
        return 1

    print(summary_table(models))
    for model, run in models.items():
        print(f"\n### {model}\n")
        print(text_table(run))
        if run["summary"]["llm_first_error"]:
            print(f"\nFirst model error: {run['summary']['llm_first_error']}")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"texts-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    out_file.write_text(
        json.dumps(
            {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "texts_file": str(args.texts),
                "prompt_file": str(args.prompt),
                "llm_timeout_s": args.timeout,
                "hold_at": HOLD_AT,
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
