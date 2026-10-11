"""The privacy policy and the support page: two public pages, linked from the iPhone app.

What a privacy policy has to say depends on how a server is set up: which company turns
speech into text, which model scores a call, whether texts are kept. So the policy is written
from the settings. It cannot name a service this server does not use, or leave out one it does.

The words are in privacy.html and support.html. This module fills in what varies:
`{{slot}}` is replaced, and an element marked `data-when="fact"` is kept only where that
fact holds for this server (see `facts`). A marked element must not hold another of its
own kind: a marked <p> holds no <p>, a marked <div> no <div>, a marked <span> no <span>.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path

from config import Settings
from scorer.providers import resolve

ROOT = Path(__file__).resolve().parent

# Who is behind each model provider in scorer/providers.
MODEL_COMPANIES = {
    "anthropic": "Anthropic",
    "openai": "OpenAI",
    "google": "Google",
    "groq": "Groq",
    "vultr": "Vultr",
    "vultr-decision": "Vultr",
    "typesafe": "TypeSafe AI",
}
UNNAMED_MODEL_SERVICE = "A model service chosen by whoever runs this server"


@dataclass(frozen=True)
class Service:
    """Another company this server hands information to, and what it is handed."""

    name: str
    receives: str


def model_companies(settings: Settings) -> list[str]:
    """The companies whose models score calls and texts here. None when only the rules do."""
    companies: list[str] = []
    for spec in (settings.scorer_model, settings.scorer_backup_model):
        if not spec or spec.lower() in ("rules", "none"):
            continue
        try:
            company = MODEL_COMPANIES.get(resolve(spec)[0], UNNAMED_MODEL_SERVICE)
        except ValueError:  # a model name the server itself cannot place
            company = UNNAMED_MODEL_SERVICE
        if company not in companies:
            companies.append(company)
    return companies


def services(settings: Settings, texts_on: bool) -> list[Service]:
    """Every company this server, as it is set up, hands information to.

    `texts_on` is whether text protection is running, which the settings alone do not say:
    asked for with a wrong setting, it stays off.
    """
    by_imessage = texts_on and settings.text_forward_via == "imessage"
    found: list[Service] = []
    if settings.twilio_account_sid:
        found.append(Service(
            "Twilio",
            "Carries the calls and texts. It handles the phone numbers on each one, the sound of a "
            "call while it is connected, and the words of a text.",
        ))
    if settings.stt_provider == "deepgram" and settings.deepgram_api_key:
        # stt/deepgram.py does not send Deepgram's `mip_opt_out=true`. If it ever does, the
        # second sentence here is no longer true and has to go.
        found.append(Service(
            "Deepgram",
            "Turns the sound of a call into written words while the call happens. Deepgram may keep "
            "audio to improve its own models, under the terms of the account this server uses; "
            "this server does not ask it not to.",
        ))
    companies = model_companies(settings)
    if companies:
        # One entry for the model that scores and the one behind it: they are sent the same.
        several = len(companies) > 1
        found.append(Service(
            " and ".join(companies),
            f"{'Score' if several else 'Scores'} how likely a scam is. {'They are' if several else 'It is'} "
            "sent the words spoken on a call as they are written down, and the words of a text that is "
            "being checked, with no phone numbers or names unless someone says or writes them.",
        ))
    if settings.auth0_domain:
        found.append(Service(
            "Auth0, by Okta",
            "Runs sign-in for caregivers. It holds your email address, your password if you chose one, "
            "and, if you sign in with Google, the name and picture Google shares.",
        ))
    if texts_on and settings.photon_bridge_token:
        found.append(Service(
            "Photon",
            "Sends the caregiver an iMessage when a text is held: the caregiver’s number and a fixed "
            "sentence, never the text’s words."
            + (
                " It also delivers approved texts to the protected person, so those texts and their "
                "senders’ numbers pass through it."
                if by_imessage
                else ""
            ),
        ))
    if settings.hosted_by:
        found.append(Service(
            settings.hosted_by,
            "Provides the computer this server runs on, so what the server keeps is stored there.",
        ))
    return found


def facts(settings: Settings, texts_on: bool) -> dict[str, bool]:
    """What is true of this server, by the names the pages use in `data-when`."""
    known = {
        "operator": bool(settings.operator_name),
        "contact": bool(settings.support_email),
        "texts": texts_on,
        "sign-in": bool(settings.auth0_domain),
        "token": bool(settings.events_token) and not settings.auth0_domain,
        # Neither a sign-in nor a token: whoever can reach the server can follow its calls.
        "guarded": bool(settings.auth0_domain or settings.events_token),
        "models": bool(model_companies(settings)),
        "logs-transcripts": settings.log_transcripts,
        "services": bool(services(settings, texts_on)),
    }
    return {**known, **{f"no-{name}": not holds for name, holds in known.items()}}


def privacy_page(settings: Settings, *, texts_on: bool) -> str:
    return _page("privacy.html", settings, texts_on)


def support_page(settings: Settings, *, texts_on: bool) -> str:
    return _page("support.html", settings, texts_on)


# A marked block goes with its line. A marked <span> sits inside a sentence, which closes up.
_MARKED_BLOCK = re.compile(r'[ \t]*<(p|li|div) data-when="([a-z-]+)">.*?</\1>\n?', re.S)
_MARKED_WORDS = re.compile(r'<(span) data-when="([a-z-]+)">.*?</span>', re.S)


_NOTE_TO_EDITORS = re.compile(r"<!--.*?-->\n?", re.S)


def _page(name: str, settings: Settings, texts_on: bool) -> str:
    known = facts(settings, texts_on)
    # The files' comments are for whoever edits them, not for the page's readers.
    page = _NOTE_TO_EDITORS.sub("", (ROOT / name).read_text(encoding="utf-8"))

    # A fact the page asks about and this module does not know is a mistake in the page:
    # better an error than a paragraph shown, or dropped, by accident.
    def keep(marked: re.Match) -> str:
        fact = marked.group(2)
        return marked.group(0).replace(f' data-when="{fact}"', "", 1) if known[fact] else ""

    page = _MARKED_WORDS.sub(keep, _MARKED_BLOCK.sub(keep, page))
    email = html.escape(settings.support_email, quote=True)
    slots = {
        "operator": html.escape(settings.operator_name),
        "contact": f'<a href="mailto:{email}">{email}</a>',
        "services": "\n".join(
            f"  <li><strong>{html.escape(service.name)}.</strong> {html.escape(service.receives)}</li>"
            for service in services(settings, texts_on)
        ),
    }
    for slot, value in slots.items():
        page = page.replace("{{" + slot + "}}", value)
    return page
