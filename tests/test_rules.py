import json
import time
from pathlib import Path

import pytest

from scorer.rules import _normalize_with_origins, highlights, normalize, score_lines, score_text

FLAGGED = [
    ("Go to the store and buy gift cards, then read me the numbers on the back.", "UNUSUAL_PAYMENT"),
    ("You need to buy get cards from the pharmacy.", "UNUSUAL_PAYMENT"),  # speech-to-text slip
    ("You can pay the fine with a pre-paid debit card.", "UNUSUAL_PAYMENT"),
    ("Deposit the cash into a Bitcoin ATM near you.", "UNUSUAL_PAYMENT"),
    ("Please wire the full balance today.", "UNUSUAL_PAYMENT"),
    ("An agent will come by to pick up the cash.", "UNUSUAL_PAYMENT"),
    ("We need to move your savings into a secure holding account.", "SAFE_ACCOUNT_TRANSFER"),
    ("Please install any desk so I can fix it.", "REMOTE_ACCESS"),
    ("Download TeamViewer and I will take a look.", "REMOTE_ACCESS"),
    ("I need remote access to remove the virus.", "REMOTE_ACCESS"),
    ("What's the six digit code we just texted you?", "OTP_REQUEST"),
    ("Read me the verification code.", "OTP_REQUEST"),
    ("We sent a code to your phone. Please read it to me.", "OTP_REQUEST"),
    ("Can I have your social security number?", "CREDENTIAL_REQUEST"),
    ("I need you to confirm your full card number and the PIN.", "CREDENTIAL_REQUEST"),
    ("What is your online banking password?", "CREDENTIAL_REQUEST"),
    ("Don't tell your family about this.", "SECRECY"),
    ("If the teller asks, say it's for home repairs.", "SECRECY"),
    ("You must keep this confidential.", "SECRECY"),
    ("Stay on the line and do not hang up.", "STAY_ON_LINE"),
    ("There is a warrant out for your arrest.", "THREAT"),
    ("Your power will be shut off this afternoon.", "THREAT"),
    ("You must act immediately.", "URGENCY_PRESSURE"),
    ("This is Officer Smith from the Social Security Administration.", "AUTHORITY_IMPERSONATION"),
    ("Grandma, I was in an accident and I need money for a lawyer.", "FAMILY_EMERGENCY"),
    ("Congratulations, you have won the lottery!", "PRIZE_OR_REFUND"),
    ("You only need to pay the processing fee first.", "PRIZE_OR_REFUND"),
    ("Calling the bank will alert the criminals.", "VERIFICATION_AVOIDANCE"),
    ("Do not call your bank, the staff are involved.", "VERIFICATION_AVOIDANCE"),
]

NOT_FLAGGED = [
    ("We will never ask for your password or full card number by phone.", "CREDENTIAL_REQUEST"),
    ("We will never ask you to read us a verification code.", "OTP_REQUEST"),
    ("Scammers may ask you to buy gift cards. Please hang up if that happens.", "UNUSUAL_PAYMENT"),
    ("Can you confirm the last four digits of your social?", "CREDENTIAL_REQUEST"),
    ("What's your zip code?", "OTP_REQUEST"),
    ("What's the gate code for your building?", "OTP_REQUEST"),
    ("I'll give you a confirmation code for your order.", "OTP_REQUEST"),
    ("I will give you the account number for the payment.", "CREDENTIAL_REQUEST"),
    ("Can you confirm your date of birth?", "CREDENTIAL_REQUEST"),
    ("I can't talk to him right now.", "SECRECY"),
    ("Feel free to hang up and call the number on your card.", "STAY_ON_LINE"),
    ("Please bring your insurance card to the appointment.", "UNUSUAL_PAYMENT"),
    ("Your prescription is ready for pickup.", "UNUSUAL_PAYMENT"),
]


@pytest.mark.parametrize("text, flag", FLAGGED)
def test_scam_phrases_raise_their_flag(text, flag):
    assert flag in score_text(text).flags


@pytest.mark.parametrize("text, flag", NOT_FLAGGED)
def test_ordinary_phrases_do_not_raise_the_flag(text, flag):
    assert flag not in score_text(text).flags


def test_normalize_keeps_sentence_breaks_and_drops_apostrophes():
    assert normalize("Don't hang up! Okay?") == "dont hang up . okay ."


def test_a_bare_mention_stays_under_the_warn_threshold():
    assert score_text("I got you a gift card for your birthday!").risk < 60


def test_one_strong_request_lands_between_warn_and_end():
    risk = score_text("Go and buy gift cards for me.").risk
    assert 60 <= risk < 85


def test_two_strong_signals_reach_end_territory():
    risk = score_text("Buy gift cards and read me the numbers. Do not tell your family.").risk
    assert risk >= 80
    risk = score_text(
        "This is the fraud department. Move your money to a safe account right now. Do not tell the teller."
    ).risk
    assert risk >= 85


def test_only_the_callers_words_count():
    lines = [
        ("CALLER", "Hi, is this Margaret?"),
        ("SENIOR", "Yes. Do you want me to buy gift cards and read you the numbers? Should I not tell my family?"),
    ]
    assert score_lines(lines).risk == 0


def test_flags_are_listed_strongest_first():
    result = score_text("This is the electric company. Pay right now with a prepaid debit card.")
    assert result.flags[0] == "UNUSUAL_PAYMENT"
    assert set(result.flags) == {"UNUSUAL_PAYMENT", "URGENCY_PRESSURE", "AUTHORITY_IMPERSONATION"}


def test_empty_text_scores_zero():
    assert score_text("").risk == 0
    assert score_lines([]).flags == ()


def test_rules_run_in_well_under_five_milliseconds():
    forty_seconds_of_speech = " ".join(text for text, _ in FLAGGED[:12])
    started = time.perf_counter()
    for _ in range(50):
        score_text(forty_seconds_of_speech)
    assert (time.perf_counter() - started) / 50 < 0.005


HIGHLIGHTED = [
    (
        "Go and buy gift cards, then read me the numbers. Do not tell your family.",
        ["buy gift cards, then read me the numbers", "Do not tell your family"],
    ),
    ("DON'T tell anyone about this, okay?", ["DON'T tell anyone"]),  # the caller's own spelling, not ours
    ("There is a warrant out for your arrest.", ["warrant", "arrest"]),
    ("Grandma, I was in an accident and I need money for a lawyer.", ["Grandma", "accident", "need money", "lawyer"]),
    ("Please read me the code. Read me the code again.", ["read me the code", "Read me the code"]),
    ("We will never ask you to buy gift cards.", []),  # a warning about scams, not a scam
    ("Your prescription is ready for pickup.", []),
    ("", []),
]


@pytest.mark.parametrize("text, phrases", HIGHLIGHTED)
def test_highlights_are_the_callers_words_that_matched_a_rule(text, phrases):
    assert highlights(text) == phrases


def test_every_highlight_can_be_found_again_in_the_text():
    scripts = json.loads(Path("eval/eval-scripts.json").read_text(encoding="utf-8"))["scripts"]
    lines = [turn["text"] for script in scripts for turn in script["turns"]]
    lines += ["U.S. Marshals... pay $500.00 in gift-cards?!", "İstanbul DON’T tell anyone", "e-mail me the PIN — now!!"]
    assert any(highlights(line) for line in lines)
    for line in lines:
        assert _normalize_with_origins(line)[0] == normalize(line)
        assert all(phrase and phrase in line for phrase in highlights(line))
