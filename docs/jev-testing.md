# Testing Jev against Haiku

> **Experimental feature, not how Palisade works.** Jev can be switched on as the scorer, but
> it has never answered a real request and has never been measured. Palisade keeps scoring
> with Claude Haiku. Nothing on the Vultr server or in the demo uses Jev, and nobody should
> describe it as part of Palisade until the results below are filled in and the branch is
> merged.

The branch `Omar` adds Jev, TypeSafe AI's decision model, as a scorer for calls and texts.
Jev answers fixed questions with calibrated probabilities instead of writing text. This guide
finds out whether it should replace Haiku. It has two parts: the evals, which give the numbers,
and the iPhone app in the simulator, which shows whether a call looks right with it. Allow about
an hour. Everything here runs on a Mac without Twilio, Deepgram or a phone.

What the branch adds, so you know where to look:

- `scorer/providers/__init__.py`: `jev-latest` (or `typesafe:<model>`) is sent to
  `https://api.typesafe.ai/v1/systemone` with `TYPESAFE_API_KEY`.
- `scorer/providers/vultr_decision.py`: the questions Jev is asked. Vultr's decision models
  (`vultr-decision:`) use the same code.
- `eval/text-scripts.json` (32 texts), `eval/pushy-legit-scripts.json` (5 genuine calls that
  sound like scams), `eval/run_text_eval.py`, and `eval/compare.py`, which runs every eval at once.

## 1. Get the branch

```bash
git fetch origin
git switch Omar
```

If the repo folder is open in Xcode or in another session, use a second folder instead of
switching: `git worktree add ../palisade-jev Omar`, then work in `../palisade-jev`.

## 2. Set up

```bash
uv venv && uv pip install -r requirements.txt
```

Without `uv`: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`. The commands
below use `.venv/bin/python`.

Put the keys in `.env` (git ignores it; copy `.env.example` if there is none):

```text
ANTHROPIC_API_KEY=...          # Haiku, the model Jev is compared with
TYPESAFE_API_KEY=...           # Jev: https://console.typesafe.ai/keys
VULTR_INFERENCE_API_KEY=...    # optional: autojev-27b, a Jev-family model on Vultr
```

If your `.env` already has real Twilio settings, make sure it also says `DRY_RUN_ACTIONS=true`.

## 3. Check that Jev answers at all

This is the first time this code meets TypeSafe's real API, so try one call and one text first:

```bash
.venv/bin/python eval/run_eval.py --model jev-latest --only scam_01
```

```bash
.venv/bin/python eval/run_text_eval.py --model jev-latest --only scam_01
```

Good: a table whose "LLM fallbacks" (or "Could not check") is `0/N`, and no "First LLM error"
line under it. If there is an error:

| Error | What it means | What to do |
|---|---|---|
| `Skipping jev-latest: ... needs TYPESAFE_API_KEY` | The key is not in `.env` | Add it. |
| `provider: HTTP 401` | Wrong key | New key from the console. |
| `provider: HTTP 404`, or a model not found | The model ID changed | `curl -s https://api.typesafe.ai/v1/models -H "Authorization: Bearer <key>"`, then use `--model typesafe:<id>`. |
| `provider: HTTP 422 ...` | TypeSafe refused the request; the message names the field | The request is built by `questions()` in `scorer/providers/vultr_decision.py`. The most likely fix is there. |
| `provider: unexpected answers ...` | The reply is shaped differently from TypeSafe's docs | `reply()` in the same file reads it. |
| `timeout` on most turns | Slower than the 2.5 s limit from your network | Run again with `--timeout 5` to see how slow. Live calls keep 2.5 s. |

If you fix something, run `.venv/bin/python -m pytest -q` (all tests pass on a Mac) and say
what you changed in the results.

## 4. Run the comparison

```bash
.venv/bin/python eval/compare.py
```

That runs `rules`, `claude-haiku-4-5` and `jev-latest` through four suites: the 16 call
scripts, the 5 pushy but genuine calls, the 7 prompt-injection calls and the 32 texts. With a
Vultr key, add the Vultr model:

```bash
.venv/bin/python eval/compare.py --models rules,claude-haiku-4-5,jev-latest,vultr-decision:autojev-27b
```

It takes a few minutes and costs about half a dollar of Haiku (Jev is a fraction of a cent).
It writes `eval/results/comparison-<time>.md`. The sets are small and model answers can shift
a little between runs, so run it twice and keep both reports.

How to read it. The `rules` row is the floor: on a call the risk is the higher of the rules and
the model, and on a text the model can only raise the risk. So a model is judged on what it adds
(scams the rules miss) and what it costs (false alarms, slowness).

- **Calls:** scams detected and ended out of 8, false alarms out of 8. "Seconds from trigger"
  is how soon Palisade acted after the turn where a careful listener would call it a scam
  (negative means earlier). "LLM p50 / p95" must stay well under 2.5 s. Each fallback is a
  turn scored on the rules alone.
- **Pushy but genuine calls:** every one should go through. A WARN is a miss; an END means
  hanging up on someone's doctor or bank.
- **Prompt injection:** every scam should still be ended, and the two genuine calls left alone.
- **Texts:** "Scams held" and "Ordinary texts held" (a held ordinary text waits for the
  caregiver before the senior gets it). "Model alone" leaves the rules out, which is the
  fairest way to compare the models. Each "Could not check" is a text held because the model
  did not answer.

## 5. Watch it in the iPhone simulator

First time with Xcode: `ios/README.md`, "Run it in the simulator", step 1.

Terminal 1, the server scoring with Jev:

```bash
DEMO_MODE=true DRY_RUN_ACTIONS=true SCORER_MODEL=jev-latest LOG_DIR=/tmp/palisade-jev .venv/bin/uvicorn main:app --port 8000 --timeout-graceful-shutdown 2
```

**Check the startup line says `scorer=jev-latest`.** If it says `scorer=rules only`, the key was
not found, and the server quietly scores with the rules. `curl -s localhost:8000/health` shows
the same thing as `"scorer_model"`.

Terminal 2, the scores as they happen, with the rules' number, the model's number and how
long the model took:

```bash
.venv/bin/python tools/watch.py
```

In Xcode, open `ios/Shield.xcodeproj`, choose the `Shield` scheme and an iPhone simulator, and
press Run. The app connects to `localhost:8000` by itself. Tap the play button at the top of
Summary and pick a call. One demo call plays at a time, at speaking pace (under a minute).
Play these:

| Script | What it tests | Expected |
|---|---|---|
| `scam_01` | The classic: authority, threat, secrecy, gift cards | Warning, then "Palisade Ended the Call" |
| `scam_03` | A tech-support scam that starts out ordinary | Ended once remote access comes up |
| `scam_08` | A card scam that asks for a code; the rules alone never end it | Ended |
| `legit_03` | A real bank fraud alert | Left alone |
| `legit_08` | A hospital asking for a date of birth | Left alone |

For each, note when the dial reached Suspicious, whether the call was ended, and the response
time on the "Palisade Ended the Call" screen.

Texts: open the Texts tab, tap "Check a Text" (top right), and paste a few from
`eval/text-scripts.json`, for example `scam_01_new_number_mom`, `scam_10_medicare_number`,
`legit_07_birthday_gift_card` and `legit_12_son_asks_venmo`. Note what the app says for each.

Then stop the server (Ctrl-C), start it again with `SCORER_MODEL=claude-haiku-4-5` and
`LOG_DIR=/tmp/palisade-haiku`, and play the same calls and texts.

## 6. If you want to tune it

The wording Jev is asked is in `scorer/providers/vultr_decision.py`: `CALL_LEVELS` and
`TEXT_LEVELS` describe the risk levels, `TEXT_FLAGS` the warning signs in a text (the ones for
calls come from `prompts/scam-scorer-prompt.md`). Tuning wording on these same scripts makes
the numbers look better than real life will be, so say in the results what you changed and
run the comparison again after.

## 7. Send the results back

Fill in the table below, then commit it with the reports and push:

```bash
git add docs/jev-testing.md eval/results/comparison-*.md && git commit -m "Jev: comparison results" -- docs/jev-testing.md eval/results/
```

```bash
git push origin Omar
```

## Results

Fill this in. Leave a column empty for a model you did not run.

- Date, and who ran it:
- Commit (`git rev-parse --short HEAD`):
- Anything changed in the code or wording, and why:

| | rules | Haiku | Jev | autojev-27b |
|---|---|---|---|---|
| Calls: scams detected / ended (of 8) | | | | |
| Calls: false alarms (of 8) | | | | |
| Calls: model p50 / p95 seconds | n/a | | | |
| Calls: fallbacks | n/a | | | |
| Pushy genuine calls: WARN / END (of 5) | | | | |
| Injection: scams ended (of 5) / false alarms (of 2) | | | | |
| Texts: scams held / ordinary held (of 16 each) | | | | |
| Texts, model alone: scams / ordinary over 30 | n/a | | | |
| Simulator: `scam_01` ended? response time | | | | |
| Simulator: `legit_03` and `legit_08` left alone? | | | | |
| Did the two runs agree? | | | | |

Anything that looked wrong in the app, or odd in the numbers:

## When to merge

Merging the branch does not switch Palisade to Jev: the server keeps scoring with Haiku until
`SCORER_MODEL=jev-latest` and `TYPESAFE_API_KEY` are put in `.env.vultr` and
`python tools/vultr_deploy.py push` is run. Until then Jev stays an experiment, even on main.
Make that switch only if, in both runs:

- **Calls:** Jev ends at least as many of the 8 scams as Haiku, with no false alarm on the 16
  scripts and no END on the 5 pushy calls.
- **Injection:** every injected scam is ended, and neither genuine call is touched.
- **Texts:** Jev alone holds at least as many scam texts as Haiku alone, without holding more
  ordinary ones.
- **Speed:** p95 under 1.5 s and fallbacks under 5% (the live limit is 2.5 s).
- **Simulator:** nothing looked wrong.

If Jev wins on texts but not on calls, or the other way round, say so: calls and texts share
one `SCORER_MODEL` today, and a separate setting for texts would be a small change.
