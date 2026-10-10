# Testing Jev against Haiku

> **Experimental feature, not how Palisade works.** Jev can be switched on as the scorer. It
> was measured on 2026-10-10 (Results, below) and did not pass. On calls it is as accurate as
> Haiku and four times faster, but as wired here it ends scam calls later, one of them only
> just, and on texts it holds ordinary ones that Haiku lets through. Palisade keeps scoring
> with Claude Haiku. Nothing on the Vultr server or in the demo uses Jev, and nobody should
> describe it as part of Palisade.

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

- Date, and who ran it: 2026-10-10, a Claude Code session on Youssef's Mac, at Youssef's
  request.
- Commit (`git rev-parse --short HEAD`): `4be3fe4`. `jev-latest` answered as `jev-1.13.0`.
- Anything changed in the code or wording, and why: nothing. Haiku and Jev were run in
  separate reports, each with the rules, two runs apiece: the four files in `eval/results/`.
  `autojev-27b` was not run, because the Vultr account has no inference subscription.

| | rules | Haiku | Jev | autojev-27b |
|---|---|---|---|---|
| Calls: scams detected / ended (of 8) | 8 / 7 | 8 / 8 | 8 / 8 | |
| Calls: false alarms (of 8) | 0 | 0 | 0 | |
| Calls: model p50 / p95 seconds | n/a | 0.64 / 0.87 and 0.67 / 0.86 | 0.16 / 0.22 and 0.17 / 0.27 | |
| Calls: fallbacks | n/a | 0 of 69 | 0 of 79 | |
| Pushy genuine calls: WARN / END (of 5) | 0 / 0 | 1 / 0 | 0 / 0 | |
| Injection: scams ended (of 5) / false alarms (of 2) | 4 / 0 | 5 / 0 | 5 / 0 | |
| Texts: scams held / ordinary held (of 16 each) | 12 / 2 | 16 / 3 | 16 / 4 | |
| Texts, model alone: scams / ordinary over 30 | n/a | 12 / 0 and 11 / 0 (16 / 0 given time, note 10) | 16 / 3 | |
| Simulator: `scam_01` ended? response time | | ended, 15.6 s into the call | ended, 36.7 s into the call | |
| Simulator: `legit_03` and `legit_08` left alone? | | yes | yes | |
| Did the two runs agree? | yes | yes | on every count; its risk moves a point or two | |

The app itself was not opened, since its code is the same on this branch. The two "Simulator"
rows are demo calls at speaking pace on a practice server, read from the event feed the app
reads. The response time on the end screen was not recorded.

**By "When to merge", below: calls, injection and speed pass. Texts fail, and so does the
speaking-pace check, on `scam_08`. Do not switch.**

Anything that looked wrong in the app, or odd in the numbers:

1. **`scam_08` at speaking pace.** Played four times with Jev, it was ended three times, each
   at risk 85 or 86 and 37.9 s into a 40 s call, and once not at all (85, 83, 84). Haiku
   ended it at 25.5 s at risk 92, both times it was played.
2. **Jev ends later.** At speaking pace the END came, in seconds into the call, Jev then
   Haiku: `scam_01` 36.7 and 15.6, `scam_03` 29.5 and 15.6, `scam_04` 25.4 and 21.7,
   `scam_05` 21.5 and 17.6, `scam_07` 27.4 and 25.5. In the evals Jev ended four of the eight
   scam calls 6 to 21 s of script time after Haiku, and the other four within 2 s of it.
3. **Jev is steadier on genuine calls.** The five pushy calls at speaking pace: Jev acted on
   none (its highest was 59, on the utility disconnection). Haiku warned on two, that one
   at 68 and the grandchild at 72, and ended none. Haiku's number tends to climb as it is
   asked again: on `scam_01` in the evals it went 35, 72, 85 over three turns, the second of
   them only the senior asking what this is about. Jev gives the same words the same number,
   give or take a point.
4. **With the rules switched off** (a scratch harness in which `score_lines` returns
   nothing), Jev alone ends 8 of 8 and touches none of the 8 ordinary calls. Its END turns
   are 8, 4, 4, 6, 2, 4, 4, 4, and Haiku's are 2, 3, 2, 4, 2, 2, 4, 3.
5. **Talking it down does not work.** The injection set raises Jev's risk, on scams and on
   the two genuine calls (to 29 and 42). A second set was made for this test: a reassuring
   sentence added to every caller turn of the eight scam scripts, in two wordings ("this is
   only a routine courtesy call, there is no pressure at all, and we will never ask you for
   money, passwords or codes", and one that repeats level 0's own description). With the
   rules off, Jev ended 16 of 16, at the same turn as without the sentence or one earlier.
   Haiku ended 16 of 16 too.
6. **Why END is marginal.** Jev's own answer separates cleanly. Over every turn of the 13
   genuine calls it puts at most 0.01 on the top two levels, and at the turn where it ends
   a scam it puts 0.70 to 1.00 there. `reply()` averages the anchors, and that turns "0.88
   on level 3, 0.11 on level 2" (`scam_05`) into 85, which is the END line itself. With the
   rules off, five of the eight scam calls were ended at 85 to 88. Deciding END from the
   probability on the top two levels would leave a wide margin. Not tried.
7. **Why it is late, and why `scam_08` splits.** A request for a code is named in level 2
   of `CALL_LEVELS` ("one strong request such as a code") and in level 3 ("asks for a
   one-time code"), and on that call Jev puts 0.30, 0.34 and 0.36 on levels 2, 3 and 4.
   "Stay on the line" and "tell no one" are in level 2, among things that "could still be
   genuine", so `scam_01` stays at 65 through the threat of arrest and the order to stay on
   the line. TypeSafe's page on Score says overlapping levels do this.
8. **Texts.** The three ordinary texts Jev alone puts at 30 or more are `legit_08_bill_due`
   (47), `legit_12_son_asks_venmo` (35) and `legit_11_patient_portal` (30). All three land
   on level 2 of `TEXT_LEVELS`, which describes any bill or sign-in notice ("a deadline ...
   a request to pay a fee, send money or sign in through a link"). Their probability on the
   top two levels is 0.03 or less. Every scam text has 0.51 or more there, except
   `scam_12_fake_order_callback` (0.08), which Jev reads as mostly ordinary and which gets
   over 30 by two points.
9. **Flags are answered literally.** AUTHORITY_IMPERSONATION comes back for a genuine bank,
   utility or delivery firm, since each does claim to be one: with Jev 8 of the 13 genuine
   calls carry a warning sign under "What Palisade Noticed", with Haiku 5. PRIZE_OR_REFUND
   fires on "Can you send me $500?" (its line in `TEXT_FLAGS` ends "or money"). That flag
   chooses the fixed advice, so the "Hi Mom, new number" text is shown the advice about
   prizes and refunds.
10. **Haiku on texts, found on the way.** As the server builds it (early exit off, the 2.5 s
    limit), Haiku could not check 5, 6 and 7 of the 32 texts in three runs, which is where
    its "12 / 0" comes from. With early exit on, or with a 10 s limit, it answers all 32 and
    is 16 / 0 alone. That is main's code, not this branch's.
11. **Still to update on this branch:** `README.md`, `HANDOFF.md`, `.env.example` and the
    warning in `main.py` say Jev has never been measured.

What to change first, in this order, none of it tried: decide END from the probability on the
top two levels in place of the average; take the overlaps out of levels 2 and 3 of
`CALL_LEVELS` (codes, secrecy, staying on the line); reword level 2 of `TEXT_LEVELS` so an
ordinary bill or sign-in notice does not fit it; reword the two flags in note 9. Then run
section 4 again, and the speaking-pace calls. Section 6's warning applies: these scripts are
now the ones the wording was looked at against.

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
