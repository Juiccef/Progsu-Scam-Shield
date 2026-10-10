import asyncio
import importlib.util
import json

from config import ROOT
from events import EventBus, mask_phone
from scorer.decision import END, DecisionConfig, DecisionEngine
from scorer.llm import LLMScorer, ScorerPrompt
from scorer.pipeline import CallScorer
from tests.test_llm import FakeProvider
from transcript import Segment, Transcript

PROMPT = ScorerPrompt.load(ROOT / "prompts" / "scam-scorer-prompt.md")


def reply(risk, flags=()):
    return json.dumps(
        {"risk": risk, "label": "x", "flags": list(flags), "evidence": [], "explanation": "", "confidence": 0.9}
    )


def transcript_of(*lines):
    transcript = Transcript()
    for index, (speaker, text) in enumerate(lines):
        transcript.add(Segment(speaker, text, start=index * 5.0, end=index * 5.0 + 4, heard_at=1000.0 + index))
    return transcript


def scorer_with(provider, **config):
    llm = LLMScorer("fake", PROMPT, provider=provider, timeout_s=0.05) if provider else None
    return CallScorer(llm, DecisionEngine(DecisionConfig(**config)))


SCAM = transcript_of(
    ("CALLER", "There is a warrant for your arrest. Stay on the line."),
    ("SENIOR", "Oh no."),
    ("CALLER", "Do not tell your family. Buy gift cards and read me the numbers."),
)


def test_rules_and_llm_agreeing_end_the_call_in_one_pass():
    provider = FakeProvider(reply(94, ["UNUSUAL_PAYMENT", "SECRECY"]))
    evaluation = asyncio.run(scorer_with(provider).evaluate(SCAM, now=20.0))
    assert evaluation.risk == 94 and evaluation.llm_risk == 94 and evaluation.rule_risk >= 85
    assert [action.kind for action in evaluation.actions] == [END]
    assert evaluation.flags[:2] == ["UNUSUAL_PAYMENT", "SECRECY"]
    assert evaluation.heard_at == 1002.0  # the caller's latest speech
    sent = provider.calls[0]["messages"][-1]["content"]
    assert "CALLER: There is a warrant" in sent and "SENIOR: Oh no." in sent


def test_llm_timeout_falls_back_to_rules_only():
    scorer = scorer_with(FakeProvider(reply(94), delay=0.5))
    first = asyncio.run(scorer.evaluate(SCAM, now=20.0))
    assert first.llm_risk is None and first.llm_error == "timeout"
    assert first.risk == first.rule_risk >= 85
    assert first.actions == [] and scorer.engine.awaiting_confirmation  # rules alone need a second look
    second = asyncio.run(scorer.evaluate(SCAM, now=22.5))
    assert [action.kind for action in second.actions] == [END]


def test_previous_llm_risk_is_carried_to_the_next_evaluation():
    provider = FakeProvider(reply(40))
    scorer = scorer_with(provider)
    call = transcript_of(("CALLER", "Hello, is this Margaret?"))
    asyncio.run(scorer.evaluate(call, now=5.0))
    call.add(Segment("SENIOR", "Yes, speaking.", start=6.0, end=7.0))
    asyncio.run(scorer.evaluate(call, now=8.0))
    assert "previous_risk: 0" in provider.calls[0]["messages"][-1]["content"]
    assert "previous_risk: 40" in provider.calls[1]["messages"][-1]["content"]


def test_a_recheck_with_nothing_new_reuses_the_llm_answer():
    provider = FakeProvider(reply(90))  # the LLM alone is over the END threshold; the rules are not
    scorer = scorer_with(provider)
    quiet = transcript_of(("CALLER", "I am calling about the matter we discussed."))
    first = asyncio.run(scorer.evaluate(quiet, now=20.0))
    assert first.actions == [] and scorer.engine.awaiting_confirmation
    second = asyncio.run(scorer.evaluate(quiet, now=22.5))
    assert [action.kind for action in second.actions] == [END]
    assert second.llm_reused and second.llm_risk == 90 and second.llm_latency_s is None
    assert len(provider.calls) == 1


def test_a_failed_llm_call_is_retried_on_the_recheck():
    provider = FakeProvider("not json")
    scorer = scorer_with(provider)
    quiet = transcript_of(("CALLER", "Hello there."))
    asyncio.run(scorer.evaluate(quiet, now=20.0))
    asyncio.run(scorer.evaluate(quiet, now=22.5))
    assert len(provider.calls) == 2


def test_window_drops_speech_older_than_forty_seconds():
    transcript = transcript_of(("CALLER", "Buy gift cards and read me the numbers."), ("SENIOR", "Okay."))
    scorer = scorer_with(None)
    assert asyncio.run(scorer.evaluate(transcript, now=30.0)).rule_risk >= 60
    assert asyncio.run(scorer.evaluate(transcript, now=60.0)).rule_risk == 0


def test_the_llm_is_not_called_before_the_caller_speaks():
    provider = FakeProvider(reply(10))
    evaluation = asyncio.run(scorer_with(provider).evaluate(transcript_of(("SENIOR", "Hello?")), now=3.0))
    assert provider.calls == [] and evaluation.risk == 0 and evaluation.llm_error is None


def test_in_progress_text_is_scored_and_replaced_by_the_final():
    transcript = Transcript()
    transcript.add(Segment("CALLER", "go and buy get", 0, 2, final=False))
    transcript.add(Segment("CALLER", "go and buy gift cards", 0, 3, final=False))
    assert Transcript.lines(transcript.window(5, 40)) == [("CALLER", "go and buy gift cards")]
    transcript.add(Segment("CALLER", "Go and buy gift cards for me.", 0, 4, final=True))
    transcript.add(Segment("CALLER", "Right now.", 4, 5, final=True))
    assert Transcript.lines(transcript.window(5, 40)) == [("CALLER", "Go and buy gift cards for me. Right now.")]


# --- event bus -------------------------------------------------------------


def test_transcript_text_is_never_persisted(tmp_path):
    bus = EventBus(tmp_path)
    bus.publish("transcript", call_sid="CA1", speaker="CALLER", text="buy gift cards")
    bus.publish("score", call_sid="CA1", risk=90, flags=["UNUSUAL_PAYMENT"], evidence=["buy gift cards"], explanation="x")
    bus.close()
    lines = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert [line["type"] for line in lines] == ["score"]
    assert lines[0]["risk"] == 90 and "evidence" not in lines[0] and "explanation" not in lines[0]


def test_ending_a_call_drops_its_transcript_from_memory():
    bus = EventBus()
    bus.publish("transcript", call_sid="CA1", text="secret words")
    bus.publish("score", call_sid="CA1", risk=90, evidence=["secret words"])
    bus.publish("transcript", call_sid="CA2", text="another call")
    bus.forget_call("CA1")
    assert "secret words" not in json.dumps(bus.recent())
    assert [event["type"] for event in bus.recent()] == ["score", "transcript"]


def test_subscribers_receive_events_and_slow_ones_lose_the_oldest():
    async def scenario():
        bus = EventBus()
        queue = bus.subscribe()
        for number in range(1005):
            bus.publish("tick", n=number)
        first = queue.get_nowait()
        bus.unsubscribe(queue)
        bus.publish("tick", n=-1)
        return first["n"], queue.qsize()

    assert asyncio.run(scenario()) == (5, 999)


def test_mask_phone_keeps_only_the_last_four_digits():
    assert mask_phone("+1 (404) 555-0142") == "***0142"
    assert mask_phone("") == "unknown" and mask_phone("anonymous") == "unknown"


# --- eval harness ----------------------------------------------------------


def load_harness():
    spec = importlib.util.spec_from_file_location("run_eval", ROOT / "eval" / "run_eval.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_eval_harness_scores_the_scripts_with_rules_alone():
    harness = load_harness()
    scripts = json.loads((ROOT / "eval" / "eval-scripts.json").read_text())["scripts"]
    results = [
        asyncio.run(harness.run_script(script, None, DecisionConfig(), 40.0, 2.5, False)) for script in scripts
    ]
    summary = harness.summarize(results)
    assert (summary["scam_scripts"], summary["legit_scripts"]) == (8, 8)
    assert summary["scams_detected"] == 8 and summary["false_alarms"] == 0
    assert summary["llm_calls"] == 0
    table = harness.summary_table({"rules": {"summary": summary, "scripts": results}})
    assert "| rules | 8/8 (100%) |" in table and "0/8 (0%)" in table
