"""The public pages, /privacy and /support: written from the settings, open to anyone."""

import dataclasses
import re

import pytest
from fastapi.testclient import TestClient

import policy
from main import create_app
from tests.test_server import AUTH_TOKEN, SENIOR, make_settings

EVERY_COMPANY = ("Twilio", "Deepgram", "Anthropic", "TypeSafe AI", "OpenAI", "Vultr", "Auth0", "Photon")
PAGES = {"/privacy": policy.privacy_page, "/support": policy.support_page}


def written(tmp_path, path="/privacy", texts_on=False, **env) -> str:
    """The page as policy.py writes it for these settings. No server is started, so a model
    named here is never asked anything."""
    return PAGES[path](make_settings(tmp_path, **env), texts_on=texts_on)


def text_of(page: str) -> str:
    """What a reader sees: no tags, no style sheet, no comments."""
    body = re.sub(r"<!--.*?-->|<style>.*?</style>", "", page, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", body))


def named(page: str) -> set[str]:
    return {company for company in EVERY_COMPANY if company in text_of(page)}


@pytest.mark.parametrize("path", PAGES)
def test_pages_are_served_with_no_token(tmp_path, path):
    with TestClient(create_app(make_settings(tmp_path, EVENTS_TOKEN="s3cret"), stt_factory=None)) as client:
        assert client.get("/calls").status_code == 401  # the server is guarded
        answer = client.get(path)
    assert answer.status_code == 200 and "text/html" in answer.headers["content-type"]
    assert answer.text == written(tmp_path, path, EVENTS_TOKEN="s3cret")


def test_served_policy_follows_whether_text_protection_is_running(tmp_path):
    # Asked for with nothing it needs, text protection stays off, and the policy must not say otherwise.
    with TestClient(create_app(make_settings(tmp_path, TEXT_GATEWAY_ENABLED="true"), stt_factory=None)) as client:
        assert client.get("/health").json()["gateway"]["enabled"] is False
        assert "This server does not keep texts" in text_of(client.get("/privacy").text)


@pytest.mark.parametrize("path", PAGES)
def test_pages_hold_no_secret_and_nobodys_number(tmp_path, path):
    env = {
        "EVENTS_TOKEN": "s3cret-feed-token", "CAREGIVER_EMAILS": "daughter@example.com", "DEEPGRAM_API_KEY": "dg-key",
        "AUTH0_DOMAIN": "tenant.example.auth0.com", "AUTH0_IOS_CLIENT_ID": "ios-client-id",
        "PHOTON_BRIDGE_TOKEN": "bridge-token", "GATEWAY_ENCRYPTION_KEY": "queue-key",
    }
    page = written(tmp_path, path, texts_on=True, **env)
    for private in (
        "s3cret-feed-token", AUTH_TOKEN, SENIOR, "+15555550102", "+15555550100", "daughter@example.com", "dg-key",
        "ios-client-id", "tenant.example.auth0.com", "bridge-token", "queue-key",
    ):
        assert private not in page


def test_policy_names_the_services_in_use_and_no_others(tmp_path):
    # As the Vultr server runs: Jev first, Haiku behind it, Deepgram, sign-in, texts by iMessage.
    as_deployed = written(
        tmp_path, texts_on=True,
        SCORER_MODEL="typesafe:jev-1.13.0", SCORER_BACKUP_MODEL="claude-haiku-4-5", DEEPGRAM_API_KEY="dg-key",
        AUTH0_DOMAIN="tenant.example.auth0.com", TEXT_GATEWAY_ENABLED="true", PHOTON_BRIDGE_TOKEN="bridge",
        TEXT_FORWARD_VIA="imessage", HOSTED_BY="Vultr",
    )
    assert named(as_deployed) == {"Twilio", "Deepgram", "TypeSafe AI", "Anthropic", "Auth0", "Photon", "Vultr"}
    assert "approved texts to the protected person" in text_of(as_deployed)

    # Rules only, no speech-to-text, a token in place of a sign-in, no texts.
    bare = written(tmp_path, SCORER_MODEL="rules", EVENTS_TOKEN="s3cret")
    assert named(bare) == {"Twilio"}
    assert "by an automated model" not in text_of(bare) and "the model that scores it" not in text_of(bare)

    # A demo server with no phone company behind it uses nobody at all.
    alone = written(tmp_path, SCORER_MODEL="rules", TWILIO_ACCOUNT_SID="")
    assert named(alone) == set() and "not set up to use any outside service" in text_of(alone)


def test_policy_names_a_model_company_once_and_by_its_own_name(tmp_path):
    same_company_twice = written(tmp_path, SCORER_MODEL="claude-haiku-4-5", SCORER_BACKUP_MODEL="anthropic:claude-sonnet-5-5")
    assert same_company_twice.count("Anthropic") == 1 and "<strong>Anthropic.</strong> Scores" in same_company_twice
    two_companies = written(tmp_path, SCORER_MODEL="typesafe:jev-1.13.0", SCORER_BACKUP_MODEL="claude-haiku-4-5")
    assert "<strong>TypeSafe AI and Anthropic.</strong> Score how likely" in two_companies
    assert named(written(tmp_path, SCORER_MODEL="vultr-decision:some-decision-model")) == {"Twilio", "Vultr"}
    assert named(written(tmp_path, SCORER_MODEL="jev-latest")) == {"Twilio", "TypeSafe AI"}
    assert "by an automated model" in text_of(same_company_twice)


def test_policy_does_not_fail_on_a_model_it_cannot_place(tmp_path):
    for odd in ("llama-from-nowhere", "custom:llama3.2:3b"):
        settings = dataclasses.replace(make_settings(tmp_path), scorer_model=odd)
        assert policy.UNNAMED_MODEL_SERVICE in text_of(policy.privacy_page(settings, texts_on=False))


def test_policy_says_what_is_kept_only_where_it_is_kept(tmp_path):
    without_texts = text_of(written(tmp_path))
    assert "This server does not keep texts" in without_texts and "up to 24 hours" not in without_texts
    with_texts = text_of(written(tmp_path, texts_on=True, PHOTON_BRIDGE_TOKEN="bridge"))
    assert "up to 24 hours" in with_texts and "This server does not keep texts" not in with_texts
    assert "Photon" in with_texts
    assert "approved texts to the protected person" not in with_texts  # forwarding is by SMS unless set

    assert "writes the words of each call into its own log" not in without_texts
    assert "writes the words of each call into its own log" in text_of(written(tmp_path, LOG_TRANSCRIPTS="true"))


def test_policy_says_so_when_anyone_can_follow_calls(tmp_path):
    open_server = text_of(written(tmp_path))
    assert "anyone who can reach it can follow its calls" in open_server
    assert "Only a caregiver the server lets in" not in open_server
    for guard in ({"EVENTS_TOKEN": "s3cret"}, {"AUTH0_DOMAIN": "tenant.example.auth0.com"}):
        guarded = text_of(written(tmp_path, **guard))
        assert "Only a caregiver the server lets in" in guarded
        assert "anyone who can reach it can follow its calls" not in guarded


def test_policy_describes_the_way_in_this_server_uses(tmp_path):
    signed_in = text_of(written(tmp_path, AUTH0_DOMAIN="tenant.example.auth0.com", EVENTS_TOKEN="still-set"))
    assert "A caregiver signs in" in signed_in and "in place of a sign-in" not in signed_in
    by_token = text_of(written(tmp_path, EVENTS_TOKEN="s3cret"))
    assert "in place of a sign-in" in by_token and "A caregiver signs in" not in by_token


@pytest.mark.parametrize("path", PAGES)
def test_pages_name_who_runs_the_server_and_how_to_reach_them(tmp_path, path):
    page = written(tmp_path, path, OPERATOR_NAME="Ada & <Sons>", SUPPORT_EMAIL="help@palisade.example")
    assert "Ada &amp; &lt;Sons&gt;" in page and "<Sons>" not in page  # a name is text, never markup
    assert 'href="mailto:help@palisade.example"' in page

    unnamed = written(tmp_path, path)
    assert "whoever gave you" in text_of(unnamed) and "mailto:" not in unnamed


@pytest.mark.parametrize("path", PAGES)
def test_no_slot_is_left_unfilled_and_every_fact_asked_about_is_known(tmp_path, path):
    everything = {
        "OPERATOR_NAME": "Ada", "SUPPORT_EMAIL": "help@palisade.example", "AUTH0_DOMAIN": "tenant.example.auth0.com",
        "SCORER_MODEL": "claude-haiku-4-5", "PHOTON_BRIDGE_TOKEN": "bridge", "LOG_TRANSCRIPTS": "true",
    }
    for texts_on, env in ((False, {}), (True, everything)):
        page = written(tmp_path, path, texts_on=texts_on, **env)
        assert "{{" not in page and "}}" not in page
        assert "data-when" not in page and "<!--" not in page  # the editors' marks stay in the file
    # A fact a page asks about and policy.py does not know raises, so it cannot go unnoticed.
    source = (policy.ROOT / path.strip("/")).with_suffix(".html").read_text(encoding="utf-8")
    asked = set(re.findall(r'data-when="([a-z-]+)"', re.sub(r"<!--.*?-->", "", source, flags=re.S)))
    assert asked and asked <= set(policy.facts(make_settings(tmp_path), texts_on=False))


def test_the_two_pages_link_to_each_other(tmp_path):
    assert 'href="support"' in written(tmp_path, "/privacy")
    assert 'href="privacy"' in written(tmp_path, "/support")
