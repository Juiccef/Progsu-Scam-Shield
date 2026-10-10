import asyncio
from types import SimpleNamespace

from twilio.base.exceptions import TwilioRestException

from actions import END_MESSAGE, Actions, reason_for
from config import Settings
from events import EventBus


class FakeTwilio:
    """Records what would be sent to Twilio's REST API."""

    def __init__(self, *, fail_calls=(), fail_sms=False, legs=()):
        self.updates = []  # (call sid, twiml)
        self.messages_sent = []
        self.fail_calls, self.fail_sms, self.legs = set(fail_calls), fail_sms, list(legs)
        self.calls = self._Calls(self)
        self.messages = SimpleNamespace(create=self._create_message)

    class _Calls:
        def __init__(self, outer):
            self.outer = outer

        def __call__(self, sid):
            return SimpleNamespace(update=lambda twiml: self.outer._update(sid, twiml))

        def list(self, parent_call_sid, limit):
            return self.outer.legs

    def _update(self, sid, twiml):
        if sid in self.fail_calls:
            raise TwilioRestException(404, f"/Calls/{sid}", "Call is not in-progress", code=21220)
        self.updates.append((sid, twiml))

    def _create_message(self, **params):
        if self.fail_sms:
            raise TwilioRestException(400, "/Messages", "blocked: to +15555550102", code=30034)
        self.messages_sent.append(params)
        return SimpleNamespace(sid="SMtest")


def make_actions(fake, **overrides):
    env = {
        "TWILIO_ACCOUNT_SID": "ACtest",
        "TWILIO_AUTH_TOKEN": "token",
        "TWILIO_PROXY_NUMBER": "+15555550100",
        "CAREGIVER_PHONE": "+15555550102",
        "SENIOR_NAME": "Margaret",
        "SAY_VOICE": "default",
    }
    env.update(overrides)
    return Actions(Settings.from_env(env), EventBus(), client_factory=lambda: fake, public_host=lambda: "shield.example.test")


REASON = reason_for(["UNUSUAL_PAYMENT", "SECRECY", "THREAT"])


def test_reason_names_the_two_most_telling_flags():
    assert REASON == "The caller asked for payment by gift card, wire, crypto or cash and demanded secrecy."
    assert reason_for(["URGENCY_PRESSURE"]) == "The caller applied time pressure."
    assert reason_for([]) == "The caller's requests matched common scam patterns."
    assert reason_for(["NOT_A_FLAG"]) == "The caller's requests matched common scam patterns."


def test_end_speaks_to_the_senior_then_hangs_up_and_texts_the_caregiver():
    fake = FakeTwilio()
    result = asyncio.run(make_actions(fake).end("CAparent", "CAchild", REASON))
    assert result["call_control"] == "child_redirect" and result["sms"] == "queued: SMtest"
    ((sid, twiml),) = fake.updates
    assert sid == "CAchild"
    assert f"<Say>{END_MESSAGE}</Say><Hangup />" in twiml
    (sms,) = fake.messages_sent
    assert sms["to"] == "+15555550102" and sms["from_"] == "+15555550100"
    assert sms["body"] == f"Shield ended a likely scam call to Margaret. {REASON}"
    assert sms["status_callback"] == "https://shield.example.test/sms/status"


def test_end_falls_back_to_hanging_up_the_caller_when_the_senior_leg_cannot_be_redirected():
    fake = FakeTwilio(fail_calls={"CAchild"})
    result = asyncio.run(make_actions(fake).end("CAparent", "CAchild", REASON))
    assert result["call_control"] == "parent_hangup"
    assert fake.updates == [("CAparent", "<Response><Hangup/></Response>")]


def test_end_looks_up_the_senior_leg_when_no_status_callback_arrived():
    legs = [SimpleNamespace(sid="CAold", status="completed"), SimpleNamespace(sid="CAfound", status="in-progress")]
    fake = FakeTwilio(legs=legs)
    result = asyncio.run(make_actions(fake).end("CAparent", None, REASON))
    assert result["call_control"] == "child_redirect" and fake.updates[0][0] == "CAfound"


def test_end_reports_failure_instead_of_raising():
    fake = FakeTwilio(fail_calls={"CAchild", "CAparent"})
    result = asyncio.run(make_actions(fake).end("CAparent", "CAchild", REASON))
    assert result["call_control"] == "failed: Twilio error 21220 (HTTP 404)"
    assert result["sms"].startswith("queued")  # the caregiver is still told


def test_warn_texts_the_caregiver_and_survives_a_blocked_sms():
    fake = FakeTwilio()
    assert asyncio.run(make_actions(fake).warn("CAparent", REASON)) == {"sms": "queued: SMtest"}
    assert fake.messages_sent[0]["body"].startswith("Shield warning: a call to Margaret shows signs of a scam.")
    assert fake.updates == []  # a warning never touches the call

    blocked = asyncio.run(make_actions(FakeTwilio(fail_sms=True)).warn("CAparent", REASON))
    assert blocked == {"sms": "failed: Twilio error 30034 (HTTP 400)"}  # and no phone number in the message


def test_dry_run_sends_nothing():
    fake = FakeTwilio()
    actions = make_actions(fake, DRY_RUN_ACTIONS="true")
    assert not actions.live
    result = asyncio.run(actions.end("CAparent", "CAchild", REASON))
    assert (result["call_control"], result["sms"]) == ("dry_run", "dry_run")
    assert fake.updates == [] and fake.messages_sent == []


def test_no_caregiver_number_means_no_sms():
    fake = FakeTwilio()
    result = asyncio.run(make_actions(fake, CAREGIVER_PHONE="").warn("CAparent", REASON))
    assert result["sms"].startswith("skipped") and fake.messages_sent == []
