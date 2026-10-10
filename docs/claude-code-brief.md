# Shield backend: brief for Claude Code

You are building the backend for **Shield**, a hackathon project (36 hours, demo-first) that protects older adults from phone scams.

A caller dials a Shield proxy number (Twilio). Shield bridges the call to the senior's phone, listens to the audio in real time, transcribes it, scores it for scam behavior, and if a scam is detected it warns the senior and ends the call. We never store audio.

Priority order: (1) a working end-to-end demo, (2) low latency from a scam phrase being spoken to the alert or hang-up, (3) measurable accuracy on a test set, (4) polish. Cut scope before you cut reliability.

Two files should sit in the repo for you to use:
- `prompts/scam-scorer-prompt.md` (system prompt + output schema for the LLM scorer)
- `eval/eval-scripts.json` (16 synthetic call scripts: 8 scam, 8 legitimate)

If they are in the repo root, move them to those paths.

## Ask me before you start

Ask these in one message, then proceed with sensible defaults for anything I leave blank:
1. Which speech-to-text provider do we have an API key for (Deepgram, Google Cloud Speech-to-Text, other)?
2. Which LLM key do we have (OpenAI, Google, Anthropic)? Default to OpenAI `gpt-4o-mini`, but keep the scorer pluggable so we can A/B models.
3. Twilio account SID, auth token, the proxy number, and two verified phone numbers (SENIOR_PHONE, CAREGIVER_PHONE). Twilio trial accounts can only call and text verified numbers.
4. Tunnel tool for local webhooks (ngrok or cloudflared).

## Stack

- Python 3.11+, FastAPI + uvicorn, the `twilio` SDK, pytest.
- Prefer an STT provider that accepts mu-law 8 kHz audio directly so we skip transcoding. If you must transcode, note `audioop` was removed in Python 3.13 (use `audioop-lts` or numpy).
- Keep dependencies few. No database; keep call state in memory and write event logs as JSONL.

## Call flow

1. `POST /voice/incoming` (Twilio webhook on the proxy number) returns TwiML that:
   - speaks a short disclosure to the caller ("This call is monitored by Shield, a scam protection service."),
   - starts a Media Stream (`<Start><Stream url="wss://PUBLIC_HOST/media" track="both_tracks"/>`),
   - then `<Dial>`s SENIOR_PHONE. Put a `statusCallback` on the `<Number>` so we learn the child call leg SID, and set TwiML after `<Dial>` to a goodbye `<Say>` + `<Hangup/>`.
2. `WS /media` receives Twilio Media Streams events (`connected`, `start`, `media`, `stop`). Media payloads are base64 mu-law, 8 kHz, mono. Check the `track` field to label speakers: with `both_tracks` we should get the caller's audio and the senior's audio separately. **Verify this with a real test call and log what you see before building on it.**
3. Forward each track's audio to a streaming STT session. Keep partial and final transcripts with timestamps and a speaker label (`CALLER` / `SENIOR`).
4. On each finalized CALLER utterance, and at least every 2.5 seconds while new text arrives, run the scorer on the last ~40 seconds of speaker-labeled transcript.
5. The decision engine may trigger actions (below).
6. On `stop`, clean up the session.

Validate the `X-Twilio-Signature` header on every webhook. Do not log full phone numbers (mask all but the last 4 digits).

## Scoring and decisions

- `scorer/rules.py`: weighted regexes for the flag IDs defined in the prompt file (gift cards, wire, crypto, remote access, read me the code, do not tell anyone, safe account, stay on the line, etc.). Returns `rule_risk` 0-100 and flags in under 5 ms. Must tolerate STT errors where cheap.
- `scorer/llm.py`: loads `prompts/scam-scorer-prompt.md`, calls the model at temperature 0 with structured JSON output, 2.5 s timeout. On timeout or parse failure, fall back to rules only and log it. Pass `previous_risk` for stability.
- `scorer/decision.py`: `risk = max(llm_risk, rule_risk)`. Defaults, all configurable by env and tuned later using the eval harness:
  - WARN at risk >= 60, END at risk >= 85,
  - each requires 2 consecutive evaluations at or above the threshold,
  - do not act in the first 10 seconds of a call,
  - at most one WARN and one END per call,
  - numbers in `ALLOWLIST` skip monitoring entirely.
- Write unit tests for the decision logic (thresholds, debounce, allowlist, one-shot).

## Actions (`actions.py`)

- **WARN**: SMS to CAREGIVER_PHONE with a one-sentence reason, plus a dashboard event. If SMS is blocked (trial limits or US messaging registration), fall back to dashboard + log and keep going.
- **END**: update the child call leg via the Twilio REST API with TwiML that speaks a short message to the senior ("Shield has detected a likely scam and is ending this call. You are safe. Your caregiver has been notified.") followed by `<Hangup/>`. When the child leg ends, the parent leg should fall through to the post-`<Dial>` TwiML. If updating the child leg does not work, fall back to updating the parent call with `<Hangup/>`. **Test both on a real call.** Also send the caregiver SMS.
- Keep the senior's spoken message short and calm. Never read the scam text back to the senior.

## Dashboard and metrics

- `GET /` serves a single static page (vanilla JS, no build step) fed by `GET /events` (server-sent events): live transcript with speaker labels, a risk meter, flags, and WARN/END events.
- Log per-call timestamps for each stage: audio received, STT final, scorer returned, action sent. `GET /metrics/last` returns seconds from the triggering utterance to the action. We will put this number on our slide.
- Store no audio. Transcripts live in memory for the call and in eval logs only.

## Eval harness

`eval/run_eval.py --model gpt-4o-mini --prompt prompts/scam-scorer-prompt.md`
- For each script in `eval/eval-scripts.json`, feed turns cumulatively in timestamp order (simulated time by default; `--realtime` optional) through the same scorer and decision logic used live.
- Report: scam detection rate (WARN or END before the script ends), END rate, false alarm rate on legitimate scripts (any WARN or END), median turns-to-detect, and median seconds from `trigger_turn` to detection. Print a markdown table and write `eval/results/<timestamp>.json`.
- `--models a,b,c` runs a comparison so we can pick a model by measured speed and accuracy.
- `trigger_turn` is the index of the earliest turn where a careful listener should call it a likely scam. It is a judgment call; adjust if you disagree.

## Milestones (do them in order, check each before moving on)

- **M0**: skeleton, `.env.example`, `/health`, tunnel, Twilio webhook returns TwiML. *Done when* calling the proxy number rings SENIOR_PHONE and the call is bridged both ways.
- **M1**: media WebSocket connected. *Done when* logs show audio frames for both tracks during a test call.
- **M2**: live transcript with speaker labels on the dashboard.
- **M3**: rules + LLM scorer + eval harness. *Done when* the eval table runs end to end and I can see baseline numbers.
- **M4**: WARN and END actions. *Done when* a teammate reading a scam script gets the call ended with the spoken message and the caregiver SMS (or dashboard fallback).
- **M5**: dashboard polish, latency metrics, allowlist, disclosure.

Stretch, only after M5:
- `GET /scam-numbers` returning a JSON list of known scam numbers for the iOS Call Directory extension.
- `POST /token` to issue Twilio Voice SDK access tokens for a softphone (browser first, iOS later) plus a TwiML app for outgoing calls through Shield.
- Conference-based bridging so Shield can whisper a warning to the senior only.

## Constraints

- No secrets in the repo; everything via env vars.
- No outbound calls or texts to numbers that have not agreed to receive them. Demo only on verified teammate phones.
- Commit small and often. Write a README with exact setup and demo steps.
- Do not build the iOS app in this pass.
- When something about Twilio's behavior is uncertain, write a tiny test or log what actually happens rather than guessing.
