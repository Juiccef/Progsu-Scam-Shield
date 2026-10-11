#!/usr/bin/env python3
"""Run every eval for a set of models and write one report to share.

    python eval/compare.py                                  # rules, claude-haiku-4-5, jev-latest
    python eval/compare.py --models rules,claude-haiku-4-5,jev-latest,vultr-decision:autojev-27b

Four suites, each with the same models: the 16 call scripts, the pushy but genuine calls, the
prompt-injection calls, and the 32 texts. The report goes to eval/results/comparison-<time>.md,
which git keeps (the JSON files next to it it does not), so it can be committed and read by
whoever decides. With Haiku in the list a run costs roughly half a dollar; Jev costs a fraction
of a cent.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import Settings  # noqa: E402
from scorer.providers import key_vars, resolve  # noqa: E402

DEFAULT_MODELS = "rules,claude-haiku-4-5,jev-latest"
SUITES = (
    ("Calls", "The 16 call scripts the README's baseline comes from.", ["eval/run_eval.py"]),
    (
        "Pushy but genuine calls",
        "Real callers with a deadline, a threat or money in the call. Every one should go through untouched.",
        ["eval/run_eval.py", "--scripts", "eval/pushy-legit-scripts.json"],
    ),
    (
        "Prompt injection",
        "Callers who talk to the scoring model. Scams should still be ended; the two genuine calls left alone.",
        ["eval/run_eval.py", "--scripts", "eval/injection-scripts.json"],
    ),
    ("Texts", "Single texts. A scam should be held (risk 30 or more), an ordinary text forwarded.", ["eval/run_text_eval.py"]),
)


def git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def keys_line(models: list[str]) -> str:
    """Which API keys each model needs and whether they are set. Never the values."""
    seen = []
    for model in models:
        if model == "rules":
            continue
        try:
            names = key_vars(resolve(model)[0])
        except ValueError:
            seen.append(f"{model}: unknown provider")
            continue
        state = "set" if any(os.environ.get(name) for name in names) else "NOT SET"
        seen.append(f"{model}: {' or '.join(names)} {state}")
    return "; ".join(seen) or "none needed"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", default=DEFAULT_MODELS, help=f"comma-separated model specs (default: {DEFAULT_MODELS})")
    parser.add_argument("--out", default=str(ROOT / "eval" / "results"))
    args = parser.parse_args()
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    settings = Settings.from_env()  # also loads .env, so the keys below are the ones the evals see
    decision = settings.decision

    dirty = " (with uncommitted changes)" if git("status", "--porcelain", "--untracked-files=no") else ""
    lines = [
        f"# Scorer comparison, {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        f"- Branch `{git('rev-parse', '--abbrev-ref', 'HEAD')}`, commit `{git('rev-parse', '--short', 'HEAD')}`{dirty}",
        f"- Models: {', '.join(f'`{m}`' for m in models)}",
        f"- Keys: {keys_line(models)}",
        f"- Calls: WARN at {decision.warn_threshold}, END at {decision.end_threshold}, "
        f"{decision.consecutive_required} confirmations, model timeout {settings.llm_timeout_s} s",
        "- Texts: held at risk 30 or more, or when the model did not answer",
        "",
    ]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    failed = False
    for title, about, command in SUITES:
        print(f"== {title}", file=sys.stderr)
        run = subprocess.run(
            [sys.executable, *command, "--models", ",".join(models), "--out", args.out],
            cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8",
        )
        sys.stderr.write(run.stderr)
        lines += [f"## {title}", "", about, ""]
        skipped = [line for line in run.stderr.splitlines() if line.startswith("Skipping ")]
        if skipped:
            failed = True
            lines += [f"**{line}**" for line in skipped] + [""]
        if run.returncode == 0:
            lines += [run.stdout.strip().replace("\n### ", "\n#### "), ""]
        else:
            failed = True
            lines += ["**This suite did not run.** What it said:", "", "```", run.stderr.strip()[-2000:], "```", ""]

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = out_dir / f"comparison-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nWrote {report.relative_to(ROOT) if report.is_relative_to(ROOT) else report}", file=sys.stderr)
    if failed:
        print("Not every model ran in every suite; the report says which.", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
