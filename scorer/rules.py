"""Regex scam rules: instant, deterministic, and tolerant of common speech-to-text errors.

Only the CALLER's words are scored. Each flag has several weighted patterns. A
flag is worth its heaviest matching pattern, and flags combine as independent
evidence (noisy-OR), so one weak signal stays low and combinations climb fast.

Calibration follows the scorer prompt: a bare mention of a risky topic stays
under the WARN threshold, one request-shaped strong signal lands in the 60s,
and two strong signals (or one plus pressure) reach END territory.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .flags import CALLER

_APOSTROPHES = re.compile(r"[’'`]")
_SENTENCE_END = re.compile(r"[.!?]+")
_JUNK = re.compile(r"[^a-z0-9$.]+")
_SPACES = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Lowercase, drop apostrophes ("don't" -> "dont"), keep sentence breaks as " . "."""
    text = _APOSTROPHES.sub("", text.lower())
    text = _SENTENCE_END.sub(" . ", text)
    text = _JUNK.sub(" ", text)
    return _SPACES.sub(" ", text).strip()


def _g(n: int) -> str:
    """A gap of up to n characters that stays inside one sentence."""
    return r"[^.]{0,%d}" % n


# Imperative negation ("do not tell", "you cannot call"). Plain "I can't talk" is not included.
NEG = (
    r"(?:do not|dont|never|must not|mustnt|should not|shouldnt|you cannot|you cant|you can not"
    r"|not to|not allowed to|not supposed to|no need to)"
)

GIFT_CARD = (
    r"(?:(?:gift|gif|get)\s?cards?|giftcards?|gift\s(?:car|cars|guards?|certificates?)"
    r"|(?:google play|itunes|i tunes|steam|vanilla|razer gold)\s(?:gift\s)?cards?"
    r"|green ?dot|money ?pak|reloadit"
    r"|pre ?paid\s(?:debit\s|visa\s|credit\s|gift\s|master ?card\s)?cards?"
    r"|(?:reload|reloadable|cash reload)\s(?:card|pack)s?)"
)
CRYPTO = r"(?:bitcoin|bit coin|crypto ?currenc(?:y|ies)|crypto|ethereum|usdt|tether)"
WIRE_REQUEST = (
    r"(?:(?:wire|wiring)\s(?:the|your|it|me|us|that|this|them|money|funds|payment|cash|all|\d)"
    r"|(?:by|via|through|send a|send the|sending a|make a|make the|do a)\s(?:bank\s)?wire(?: transfer)?\b"
    r"|western union|money ?gram)"
)

# A code that is safe to ask about ("zip code", "gate code") is excluded.
CODE = (
    r"(?<!zip )(?<!area )(?<!postal )(?<!gate )(?<!door )(?<!promo )(?<!coupon )(?<!dress )(?<!bar )(?<!qr )"
    r"(?:code|otp|one time pass(?:word|code))\b"
)

CREDENTIAL = (
    r"(?:social security(?: number)?|social number|your social|ssn|s s n"
    r"|(?:full |complete |entire |whole |sixteen digit |16 digit |debit |credit |bank )*card number"
    r"|(?:bank |checking |savings |full |complete )*account number|routing number"
    r"|pin(?: number| code)?|password|pass ?code|pass word|cvv|c v v|cvc"
    r"|(?:three|3) digit(?:s| code| number)? on the back|security code on the back"
    r"|medicare (?:number|id)|medicaid number"
    r"|online banking (?:log ?in|details|password|user ?name|information|credentials)"
    r"|log ?in (?:details|information|credentials|info)|user ?name|user id"
    r"|mothers maiden name)\b"
)

WHO = (
    r"(?:anyone|anybody|any one|a soul|nobody|no one|family|relatives?|bank|banker|teller|cashier|clerk"
    r"|store|employee|staff|manager|police|mom|mum|mother|dad|father|parents|son|daughter|kids|children"
    r"|husband|wife|spouse|grandpa|grandma|friends?|neighbou?rs?|lawyer|attorney|them|him|her|people|others)"
)

TROUBLE = (
    r"\b(?:accident|arrested|jail|hospital|emergency room|in trouble|lawyer|attorney|stranded|mugged|robbed"
    r"|hurt|injured|kidnapped|custody|detained|wrecked|crash(?:ed)?|dui)\b"
)
MONEY_ASK = (
    r"\b(?:need|needs|send|wire|lend|borrow|get me|bring)\b" + _g(30) + r"\b(?:money|cash|dollars|funds)\b"
)
KINSHIP = r"\b(?:grandma|grandpa|grandmother|grandfather|granny|nana|nanna|gram|grammy|papa|mom|dad|aunt|auntie|uncle)\b"

SERVICE_LOSS = (
    r"(?:shut ?off|shut down|disconnected|cut off|turned off|suspended|terminated|cancel+ed|frozen|seized"
    r"|blocked|locked|closed|revoked|deactivated)"
)


@dataclass(frozen=True)
class Rule:
    flag: str
    weight: float
    patterns: tuple[re.Pattern, ...]  # all must match somewhere in the caller's text
    guarded: bool = False  # ignore matches inside a warning ("we will never ask for...")


def _rule(flag: str, weight: float, *patterns: str, guarded: bool = False) -> Rule:
    return Rule(flag, weight, tuple(re.compile(p) for p in patterns), guarded)


RULES: tuple[Rule, ...] = (
    # --- UNUSUAL_PAYMENT -------------------------------------------------
    _rule("UNUSUAL_PAYMENT", 0.65,
          r"\b(?:buy|buying|purchase|purchasing|get|pick up|grab|pay|paying|payment|load|put)\b" + _g(60) + r"\b" + GIFT_CARD,
          guarded=True),
    _rule("UNUSUAL_PAYMENT", 0.65,
          r"\b" + GIFT_CARD + _g(60) + r"\b(?:numbers?|codes?|pins?|back of the cards?|scratch|receipts?|photo|picture)\b",
          guarded=True),
    _rule("UNUSUAL_PAYMENT", 0.40, r"\b" + GIFT_CARD, guarded=True),
    _rule("UNUSUAL_PAYMENT", 0.62,
          r"\b(?:pay|paying|payment|send|sending|transfer|deposit|buy|purchase|convert|by|via|through|using)\b" + _g(30) + r"\b" + CRYPTO + r"\b",
          guarded=True),
    _rule("UNUSUAL_PAYMENT", 0.65, r"\b(?:bitcoin|crypto) (?:atm|machine|kiosk)\b", guarded=True),
    _rule("UNUSUAL_PAYMENT", 0.40, r"\b" + CRYPTO + r"\b", guarded=True),
    _rule("UNUSUAL_PAYMENT", 0.62, r"\b" + WIRE_REQUEST, guarded=True),
    _rule("UNUSUAL_PAYMENT", 0.40, r"\bwire transfers?\b", guarded=True),
    _rule("UNUSUAL_PAYMENT", 0.65,
          r"\b(?:pick ?up|picking up|collect|collecting)\s(?:the\s|your\s|that\s)?(?:cash|money|envelope|payment|funds|gold)\b",
          guarded=True),
    _rule("UNUSUAL_PAYMENT", 0.65,
          r"\b(?:put|place|pack|hide|wrap)\b" + _g(30) + r"\b(?:cash|money|bills)\b" + _g(30) + r"\b(?:box|envelope|magazine|package|bag|book)\b",
          guarded=True),
    _rule("UNUSUAL_PAYMENT", 0.60, r"\b(?:mail|ship|overnight|fedex)\b" + _g(25) + r"\bcash\b", guarded=True),
    _rule("UNUSUAL_PAYMENT", 0.45,
          r"\b(?:send|transfer|pay)\b" + _g(40) + r"\b(?:zelle|venmo|cash ?app)\b"
          r"|\b(?:zelle|venmo|cash ?app)\s(?:me|us|it|the money|the payment)\b",
          guarded=True),
    _rule("UNUSUAL_PAYMENT", 0.30,
          r"\b(?:withdraw|take out|pull out)\b" + _g(40) + r"\b(?:cash|everything|savings|all (?:of )?(?:your|the) money)\b",
          guarded=True),

    # --- SAFE_ACCOUNT_TRANSFER -------------------------------------------
    _rule("SAFE_ACCOUNT_TRANSFER", 0.65,
          r"\b(?:move|moving|transfer|transferring|put|deposit|send|sending|shift|wire)\b" + _g(60)
          + r"\b(?:safe|secure|secured|holding|protected|protective|federal|government|safety|custody)\s"
          r"(?:holding\s|custody\s|safety\s|reserve\s)?(?:account|locker|wallet|vault)\b",
          guarded=True),
    _rule("SAFE_ACCOUNT_TRANSFER", 0.50,
          r"\b(?:protect|safeguard|secure)\s(?:your|the)\s(?:money|funds|savings|assets)\b" + _g(60) + r"\b(?:move|transfer|wire|withdraw)\b",
          guarded=True),
    _rule("SAFE_ACCOUNT_TRANSFER", 0.40, r"\b(?:safe|secure|holding|protected)\s(?:holding\s)?account\b", guarded=True),

    # --- REMOTE_ACCESS ---------------------------------------------------
    _rule("REMOTE_ACCESS", 0.65,
          r"\b(?:any ?desk|team ?viewer|ultra ?viewer|logmein|go ?to ?assist|screen ?connect|quick assist|rust ?desk"
          r"|supremo|zoho assist|ammyy|alpemix)\b",
          guarded=True),
    _rule("REMOTE_ACCESS", 0.65,
          r"\bremote(?:ly)?\s(?:access|desktop|control|support|session|connection|assistance|connect)\b",
          guarded=True),
    _rule("REMOTE_ACCESS", 0.60,
          r"\b(?:access|control|connect to|get into|log into|take over)\s(?:to\s)?your\s"
          r"(?:computer|pc|laptop|device|phone|screen|desktop|machine|ipad|tablet)\b",
          guarded=True),
    _rule("REMOTE_ACCESS", 0.50, r"\bshare your screen\b|\bscreen shar(?:e|ing)\b", guarded=True),
    _rule("REMOTE_ACCESS", 0.30,
          r"\b(?:download|install)\b" + _g(40) + r"\b(?:app|application|program|software|tool|extension)\b",
          guarded=True),

    # --- CREDENTIAL_REQUEST ----------------------------------------------
    _rule("CREDENTIAL_REQUEST", 0.65,
          r"\b(?:read|give|tell|provide|send|share|spell|recite|repeat)\s(?:me|us|out|off|back)\b" + _g(45) + r"\b" + CREDENTIAL,
          guarded=True),
    _rule("CREDENTIAL_REQUEST", 0.65,
          r"\b(?:confirm|verify|enter|type|state|say|key in|punch in|input)\b" + _g(35) + r"\b(?:your|the)\b" + _g(25) + r"\b" + CREDENTIAL,
          guarded=True),
    _rule("CREDENTIAL_REQUEST", 0.65,
          r"\b(?:i|we)\s(?:will\s|just\s|also\s|still\s|now\s)?(?:need|require|want|must have)\b" + _g(35) + r"\b" + CREDENTIAL,
          guarded=True),
    _rule("CREDENTIAL_REQUEST", 0.65, r"\bwhat(?:s| is| was)\s(?:your|the)\b" + _g(25) + r"\b" + CREDENTIAL, guarded=True),
    _rule("CREDENTIAL_REQUEST", 0.65,
          r"\b(?:can|could|may|let)\s(?:i|we|me|us)\s(?:have|get|take|grab)\b" + _g(35) + r"\b" + CREDENTIAL,
          guarded=True),

    # --- OTP_REQUEST -----------------------------------------------------
    _rule("OTP_REQUEST", 0.65,
          r"\b(?:read|give|tell|share|provide|send|forward|say|repeat)\s(?:me|us|out|off|back)\b" + _g(35) + CODE,
          guarded=True),
    # May cross a sentence break: "We are sending you a code. Please read it to me."
    _rule("OTP_REQUEST", 0.65,
          CODE + r".{0,90}\b(?:read|give|tell|send|forward|share|say|repeat)\s(?:it|that|this|them|those)"
          r"(?:\s(?:back|out|off))?\s(?:to\s)?(?:me|us)\b",
          guarded=True),
    _rule("OTP_REQUEST", 0.65, r"\bwhat(?:s| is| was)\s(?:the|that|your)\s(?:[a-z0-9]+\s){0,3}" + CODE, guarded=True),

    # --- SECRECY ---------------------------------------------------------
    _rule("SECRECY", 0.55,
          r"\b" + NEG + r"\s(?:\w+\s){0,2}?(?:tell|discuss|mention|talk|speak|say anything|share|inform|disclose|reveal)\b"
          + _g(45) + r"\b" + WHO + r"\b"),
    _rule("SECRECY", 0.55,
          r"\b(?:keep|kept|keeping)\b" + _g(25)
          + r"\b(?:secret|confidential|private|between us|between you and me|quiet|to yourself|under wraps)\b"),
    _rule("SECRECY", 0.50,
          r"\b(?:compromise|jeopardi[sz]e|interfere with|ruin)\s(?:the|this|our|an)\s(?:investigation|case|operation)\b"
          r"|\bgag order\b|\bnon ?disclosure\b"
          r"|\b(?:this|the) (?:call|matter|case|investigation|conversation) is (?:strictly )?(?:confidential|classified)\b"),
    _rule("SECRECY", 0.45, r"\bbetween (?:you and me|the two of us)\b"),
    # Coaching the senior to lie: "if the teller asks, say it is for home repairs".
    _rule("SECRECY", 0.60,
          r"\bif\s(?:the|a|any|anyone|anybody|they|someone|your)\b" + _g(30) + r"\bask(?:s|ed)?\b" + _g(50)
          + r"\b(?:say|tell (?:them|him|her)|just say)\b"),

    # --- STAY_ON_LINE ----------------------------------------------------
    _rule("STAY_ON_LINE", 0.30, r"\b(?:stay|remain|keep)\b" + _g(15) + r"\bon the (?:line|phone|call)\b"),
    _rule("STAY_ON_LINE", 0.35,
          r"\b(?:" + NEG + r"|not)\s(?:\w+\s){0,2}?(?:hang up|hanging up|disconnect|end (?:the|this) call"
          r"|put (?:the|your) phone down|leave the (?:line|call))\b"),
    _rule("STAY_ON_LINE", 0.30, r"\b(?:keep|leave) (?:the|your) (?:phone|line|call) (?:on|open|connected)\b"),

    # --- URGENCY_PRESSURE ------------------------------------------------
    _rule("URGENCY_PRESSURE", 0.25,
          r"\b(?:right now|right away|immediately|at once|straight away|as soon as possible|asap|this instant|without delay)\b"),
    _rule("URGENCY_PRESSURE", 0.30,
          r"\b(?:urgent|urgently|hurry|time sensitive|time is running out|running out of time|no time|too late"
          r"|last chance|final notice|final warning|act now|act fast|deadline)\b"),
    _rule("URGENCY_PRESSURE", 0.30, r"\b(?:within|in) (?:the next )?(?:\w+ ){0,2}(?:minutes?|hours?)\b"),
    _rule("URGENCY_PRESSURE", 0.30,
          r"\b(?:must|have to|need to|got to|gotta)\b" + _g(30) + r"\b(?:today|now|tonight|immediately)\b"
          r"|\bunless\b" + _g(50) + r"\b(?:today|now|immediately|right away)\b"),

    # --- THREAT ----------------------------------------------------------
    _rule("THREAT", 0.35,
          r"\b(?:arrest(?:ed)?|warrant|lawsuit|legal action|sued|sue you|jail|prison|deport(?:ed|ation)?"
          r"|prosecut(?:ed|ion)|criminal charges?|indict(?:ed|ment)|court (?:order|summons)|subpoena"
          r"|money laundering|drug trafficking)\b"),
    _rule("THREAT", 0.30,
          r"\b(?:will be|going to be|is being|has been|have been|about to be)\s(?:\w+\s)?" + SERVICE_LOSS + r"\b"),
    _rule("THREAT", 0.30, r"\b(?:shut ?off|disconnection|suspension|termination|foreclosure|eviction)\s(?:notice|order)\b"),

    # --- AUTHORITY_IMPERSONATION -----------------------------------------
    _rule("AUTHORITY_IMPERSONATION", 0.20,
          r"\b(?:social security (?:administration|office)|internal revenue|irs|i r s|treasury|fbi|f b i|dea"
          r"|homeland security|customs|border (?:patrol|protection)|immigration|u s marshals?"
          r"|sheriffs? (?:office|department)|police department|district attorney|attorney general"
          r"|federal (?:\w+ )?(?:office|bureau|agency|agent|reserve|trade commission|government|court|marshal)"
          r"|government (?:agency|office|official)|medicare|medicaid|badge number|case number)\b"),
    _rule("AUTHORITY_IMPERSONATION", 0.20,
          r"\b(?:this is|i am|im|my name is|speaking with|its)\s(?:special\s)?"
          r"(?:officer|agent|detective|inspector|investigator|sergeant|deputy)\s[a-z]+"),
    _rule("AUTHORITY_IMPERSONATION", 0.20,
          r"\b(?:fraud|security|risk)\s(?:department|prevention|protection|team|division|unit|center|desk)\b"
          r"|\b(?:bank|card|account) security\b"),
    _rule("AUTHORITY_IMPERSONATION", 0.20,
          r"\b(?:microsoft|windows|apple|amazon|google|paypal|norton|mcafee|geek squad)\b" + _g(25)
          + r"\b(?:support|technical|technician|security|department|team|refund|billing|help ?desk)\b"
          r"|\b(?:technical|tech|computer) support\b"),
    _rule("AUTHORITY_IMPERSONATION", 0.20,
          r"\b(?:electric|power|gas|water|utility|utilities|energy)\s(?:company|provider|department)\b|\bbilling department\b"),

    # --- FAMILY_EMERGENCY ------------------------------------------------
    _rule("FAMILY_EMERGENCY", 0.55, TROUBLE, MONEY_ASK),
    _rule("FAMILY_EMERGENCY", 0.50, r"\bbail\b|\bbondsman\b"),
    _rule("FAMILY_EMERGENCY", 0.30, KINSHIP, TROUBLE),

    # --- PRIZE_OR_REFUND -------------------------------------------------
    _rule("PRIZE_OR_REFUND", 0.65,
          r"\b(?:pay|paying|send|cover|settle)\b" + _g(40) + r"\b(?:fees?|tax|taxes|customs|duty|duties|insurance|charges?)\b"
          + _g(40) + r"\b(?:first|before|upfront|up front|in advance|to (?:release|receive|claim|collect|get|process|unlock))\b"),
    _rule("PRIZE_OR_REFUND", 0.65,
          r"\bto (?:release|receive|claim|collect|unlock)\b" + _g(40)
          + r"\b(?:prize|winnings|funds|money|refund|grant|inheritance|package)\b" + _g(60) + r"\b(?:pay|fees?|tax|taxes)\b"),
    _rule("PRIZE_OR_REFUND", 0.50,
          r"\brefund\b" + _g(60) + r"\b(?:too much|overpaid|over paid|by mistake|accidentally|extra|wrong amount)\b"
          r"|\b(?:too much|overpaid|by mistake|accidentally)\b" + _g(60)
          + r"\b(?:refund|send (?:it|the difference|the extra) back|return the (?:difference|extra|money))\b"),
    _rule("PRIZE_OR_REFUND", 0.45,
          r"\b(?:processing|release|clearance|handling|activation|registration|administrative|admin|redelivery)\sfees?\b"),
    _rule("PRIZE_OR_REFUND", 0.40,
          r"\b(?:you(?:ve| have)? won|(?:youre|you are) (?:a|the|our) (?:\w+ )?winner|sweepstakes|lottery|jackpot|prize"
          r"|winnings|publishers clearing house)\b"),
    _rule("PRIZE_OR_REFUND", 0.35,
          r"\b(?:government grant|free grant|inheritance|unclaimed (?:funds|money|property))\b"),
    _rule("PRIZE_OR_REFUND", 0.20, r"\b(?:your|a|the) refund\b"),

    # --- VERIFICATION_AVOIDANCE ------------------------------------------
    _rule("VERIFICATION_AVOIDANCE", 0.40,
          r"\b(?:calling|call|contacting|contact|telling|tell|going to|visiting|notifying)\b" + _g(40)
          + r"\b(?:will|would|could|may|might|can)\s(?:only\s)?(?:alert|tip off|warn|notify|compromise|jeopardi[sz]e)\b"),
    _rule("VERIFICATION_AVOIDANCE", 0.40,
          r"\b" + NEG + r"\s(?:\w+\s){0,2}?(?:call|contact|check with|verify with|go to|visit|ask)\b" + _g(30)
          + r"\b(?:bank|branch|police|card company|real number|official number|family|son|daughter|lawyer|anyone else)\b"),
    _rule("VERIFICATION_AVOIDANCE", 0.45,
          r"\b(?:bank|branch|teller|tellers|employees?|staff|manager)\b" + _g(40)
          + r"\b(?:involved|in on it|under investigation|compromised|corrupt|cannot be trusted|cant be trusted"
          r"|not to be trusted|working with (?:the )?(?:criminals|scammers|fraudsters|thieves))\b"),
    _rule("VERIFICATION_AVOIDANCE", 0.40,
          r"\b(?:ignore|disregard)\b" + _g(30) + r"\b(?:warnings?|message|alert|notice|pop ?up|what it says)\b"
          r"|\b(?:warning|message|alert|notice)\b" + _g(25) + r"\b(?:only|just)\s(?:for|meant for|a formality|routine)\b"),
    _rule("VERIFICATION_AVOIDANCE", 0.35,
          r"\b(?:cannot|cant|dont|do not|unable to)\s(?:call|ring|phone)\s(?:me\s|us\s|this number\s)?back\b"),
)

# A match is ignored when the caller is warning about a scam rather than running one.
_NEGATED_ASK = re.compile(
    r"\b(?:never|not|dont|doesnt|wont|wouldnt|shouldnt|no one|nobody|nor)\s(?:\w+\s){0,3}?"
    r"(?:ask|asks|asking|asked|request|requests|requesting|need|needs|require|requires|want|wants|call)\b"
)
_WARNING_TALK = re.compile(r"\b(?:scam\w*|fraudsters?|beware|con artists?)\b")
_PARTIAL_ID = re.compile(r"\blast (?:four|4|for)\b|\bending in\b")


def _is_warning(text: str, start: int, end: int) -> bool:
    sentence_start = text.rfind(" . ", 0, start)
    sentence_start = 0 if sentence_start < 0 else sentence_start + 3
    sentence_end = text.find(" . ", end)
    sentence_end = len(text) if sentence_end < 0 else sentence_end
    if _WARNING_TALK.search(text, sentence_start, sentence_end):
        return True
    if _NEGATED_ASK.search(text, sentence_start, start + 1):
        return True
    return bool(_PARTIAL_ID.search(text, start, end))


def _fires(rule: Rule, text: str) -> bool:
    first, *rest = rule.patterns
    if any(not p.search(text) for p in rest):
        return False
    for match in first.finditer(text):
        if not (rule.guarded and _is_warning(text, match.start(), match.end())):
            return True
    return False


@dataclass(frozen=True)
class RuleResult:
    risk: int  # 0-100
    flags: tuple[str, ...]  # strongest first
    weights: dict[str, float]


def score_text(caller_text: str) -> RuleResult:
    """Score everything the caller has said in the window."""
    text = normalize(caller_text)
    weights: dict[str, float] = {}
    if text:
        for rule in RULES:
            if weights.get(rule.flag, 0.0) < rule.weight and _fires(rule, text):
                weights[rule.flag] = rule.weight
    remaining = 1.0
    for weight in weights.values():
        remaining *= 1.0 - weight
    flags = tuple(sorted(weights, key=lambda f: -weights[f]))
    return RuleResult(risk=round(100 * (1.0 - remaining)), flags=flags, weights=weights)


def score_lines(lines: Iterable[tuple[str, str]]) -> RuleResult:
    """Score (speaker, text) lines; the senior's own words never add risk."""
    return score_text(" . ".join(text for speaker, text in lines if speaker == CALLER))


_WORD_OR_BREAK = re.compile(r"[a-z0-9$]+|[.!?]+")


def _normalize_with_origins(text: str) -> tuple[str, list[int]]:
    """`normalize(text)`, plus the index in `text` that each character of the result came from."""
    kept: list[str] = []
    kept_at: list[int] = []
    for index, char in enumerate(text):
        for lowered in char.lower():  # a few characters lowercase to more than one
            if lowered not in "’'`":
                kept.append(lowered)
                kept_at.append(index)
    out: list[str] = []
    origins: list[int] = []
    for token in _WORD_OR_BREAK.finditer("".join(kept)):
        if out:
            out.append(" ")
            origins.append(kept_at[token.start()])
        if token.group()[0] in ".!?":
            out.append(".")
            origins.append(kept_at[token.start()])
        else:
            out.extend(token.group())
            origins.extend(kept_at[token.start():token.end()])
    return "".join(out), origins


def highlights(caller_text: str) -> list[str]:
    """The caller's own words that set a rule off, in the order they were said.

    For showing next to the transcript, never for scoring. Each phrase is an exact slice of
    `caller_text`, so a client finds it again with a plain substring search. A rule only
    counts here when all of its patterns are in this one line.
    """
    text, origins = _normalize_with_origins(caller_text)
    if not text or text != normalize(caller_text):  # never highlight from a map that has drifted
        return []
    spans: list[tuple[int, int]] = []
    for rule in RULES:
        first, *rest = rule.patterns
        others = [pattern.search(text) for pattern in rest]
        if not all(others):
            continue
        hits = [
            match
            for match in first.finditer(text)
            if not (rule.guarded and _is_warning(text, match.start(), match.end()))
        ]
        if hits:
            spans.extend(match.span() for match in (*hits, *others))

    merged: list[list[int]] = []
    for start, end in sorted(spans):
        # Whole words only: no space or sentence break at either edge, no word cut in half.
        while start < end and text[start] in " .":
            start += 1
        while end > start and text[end - 1] in " .":
            end -= 1
        if start >= end:
            continue
        while start > 0 and text[start - 1] not in " .":
            start -= 1
        while end < len(text) and text[end] not in " .":
            end += 1
        if merged and start <= merged[-1][1] + 1:  # overlapping, or the very next word
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    phrases = [caller_text[origins[start]:origins[end - 1] + 1] for start, end in merged]
    return list(dict.fromkeys(phrases))
