#!/usr/bin/env python3
"""
Intent router — classifies user text into a Bender response intent.

Returns: (intent: str, sub_key: str | None)

Intents:
  GREETING      — hello/hi/hey etc
  AFFIRMATION   — thanks/great/nice one
  DISMISSAL     — bye/stop/goodbye
  JOKE          — tell me a joke / say something funny
  PERSONAL      — personal questions (sub_key = job/age/etc)
  TIMER         — set a timer / set an alarm / remind me in / wake me up
  TIMER_CANCEL  — cancel/stop/remove/delete a timer or alarm
  TIMER_STATUS  — how long left / what timers / any alarms
  WEATHER       — weather/forecast/rain
  NEWS          — news/headlines/what's happening
  HA_CONTROL    — Commands to control HA devices (lights, switches)
  HA_STATUS     — Read-only questions about HA device state ("is the kitchen light on")
  TIME          — what time is it / how late is it
  VISION        — scene/room awareness queries (what do you see, who's in the room)
  UNKNOWN       — everything else → AI fallback
"""

import json
import os
import re

from config import cfg
from logger import get_logger
from metrics import metrics

log = get_logger("intent")

# ---------------------------------------------------------------------------
# Pattern sets — order matters (more specific first within PERSONAL)
# ---------------------------------------------------------------------------

GREETING_PATTERNS = [
    r"\b(hello|hi|hey|howdy)\b",
    r"\bgood (morning|evening|afternoon)\b",
    r"\byou there\b",
    r"\bwake up\b",
    r"\byo\b",
]

AFFIRMATION_PATTERNS = [
    r"\bthank(s| you)\b",
    r"^(great|brilliant|awesome|cheers|nice one)$",
    r"^ok(ay)?(\s+bender)?$",
]

DISMISSAL_PATTERNS = [
    r"^bye\b",
    r"\bgoodbye\b",
    r"\bsee you\b",
    r"(?<!don't )(?<!dont )\bstop(\s+(it|bender))?\b",
    r"\bbender[,\s]+stop\b",          # "Bender stop" / "Bender, stop"
    r"\bshut up(\s+bender)?\b",
    r"\bbender[,\s]+shut up\b",       # "Bender, shut up" / "Bender shut up"
    r"\bbe quiet(\s+bender)?\b",
    r"\bthat'?s?\s*all\b",
    r"\bno more\b",
    r"^enough(\s+bender)?[.!]?$",     # "Enough" / "Enough Bender"
    r"\bstop (talking|it|now)\b",
]

JOKE_PATTERNS = [
    r"\btell me a joke\b",
    r"\bsay something funny\b",
    r"\bmake me laugh\b",
    r"\bentertain me\b",
    r"\bgive me a joke\b",
    r"\bjoke\b",
]

WEATHER_PATTERNS = [
    r"\bweather\b",
    r"\bforecast\b",
    r"\b(what'?s? it like|what is it like) outside\b",
    r"\bwill it rain\b",
    r"\btemperature outside\b",
    r"\bgoing to rain\b",
]
NEWS_PATTERNS = [
    r"\bnews\b",
    r"\bheadlines?\b",
    r"\bwhat.{0,10}happening\b",
    r"\blatest updates?\b",
]

TIMER_PATTERNS = [
    r"\bset (a |an )?(timer|alarm)\b",
    r"\btimer for\b",
    r"\balarm (for|at)\b",
    r"\bremind me in\b",
    r"\bwake me (up )?(at|in)\b",
]

TIMER_CANCEL_PATTERNS = [
    r"\bcancel (the |my )?(\w+ )?(timer|alarm)\b",
    r"\bstop (the |my )?(\w+ )?(timer|alarm)\b",
    r"\bremove (the |my )?(\w+ )?(timer|alarm)\b",
    r"\bdelete (the |my )?(\w+ )?(timer|alarm)\b",
]

TIMER_STATUS_PATTERNS = [
    r"\bhow (long|much time)\b.{0,20}\b(timer|alarm|left)\b",
    r"\bwhat timers\b",
    r"\bany (timers|alarms)\b",
    r"\btime remaining\b",
    r"\bhow long left\b",
]

HA_CONTROL_PATTERNS = [
    r"\b(turn|switch|put) (on|off)\b",
    r"\blights? (on|off)\b",
    r"\b(on|off) (the )?lights?\b",
    r"\b(radiator|thermostat|heating|trv)\b",
    r"\bset (it |the temperature )?(to )?[0-9]+ degrees?\b",
    r"\b(kitchen|office|bedroom|bathroom|hallway|conservatory|dining|lounge|garden|attic|cabin|utility|ensuite|living room)\b.{0,30}\b(light|lamp|off|on|radiator|heating)\b",
    r"\b(light|lamp)\b.{0,30}\b(kitchen|office|bedroom|bathroom|hallway|conservatory|dining|lounge|garden|attic|utility|ensuite)\b",
]

# Read-only status queries — "is the office light on", "are any lights on",
# "what's the office temperature". Checked independently of HA_CONTROL_PATTERNS
# so a status question with no imperative verb still routes to HA_STATUS.
HA_STATUS_PATTERNS = [
    r"\b(is|are|was|were)\b.{0,30}\b(light|lamp|radiator|heating|thermostat|trv|switch|plug)s?\b",
    r"\bany (lights?|switches?) (on|off)\b",
    r"\bwhat(?:'s| is) the (kitchen|office|bedroom|bathroom|hallway|conservatory|dining|lounge|garden|attic|cabin|utility|ensuite|living room|radiator|thermostat)\b.{0,15}\btemperature\b",
    r"\b(been turned|turned itself|have been turned|has been turned|was turned|were turned)\b",
    r"\b(did|has|have)\b.{0,30}\b(turn|turned)\b.{0,20}\b(light|lamp|radiator|heating)\b",
]

# Leading interrogatives + past-tense/perfect narration markers. STT often
# drops the trailing "?", so we lean on these instead of punctuation to tell
# a question/statement ("is the office light on", "the lights in the kitchen
# have been turned on") apart from an imperative ("turn on the office light").
_HA_INTERROGATIVE_LEAD_RE = re.compile(
    r"^(is|are|was|were|did|does|do|has|have|what|why|when|which|any)\b",
    re.IGNORECASE,
)
_HA_NARRATION_RE = re.compile(
    r"\b(been turned|turned itself|have been|has been|was turned|were turned)\b",
    re.IGNORECASE,
)


def is_ha_question_or_narration(text: str) -> bool:
    """True if text reads as a question about — or narration of — device
    state, rather than a command to change it. Used to keep HA_CONTROL from
    firing (and toggling real devices) on questions/statements."""
    t = text.strip().lower()
    return bool(_HA_INTERROGATIVE_LEAD_RE.match(t) or _HA_NARRATION_RE.search(t))

VISION_PATTERNS = [
    r"\bwhat (do you see|can you see)\b",
    r"\bwho('?s| is) in the room\b",
    r"\bdescribe (the room|what you see)\b",
    r"\blook around\b",
    r"\bwhat('?s| is) (in front of|around) you\b",
]

PERSONAL_PATTERNS = [
    ("eat",         r"\b(eat|food|drink|hungry|alcohol|beer|fuel|power)\b"),
    ("feelings",    r"\b(feel(ings?)?|emotion(al)?|happy|sad|angry|upset|care)\b|\bhow are you\b"),
    ("like_me",     r"\bdo you like me\b|\byou like me\b"),
    ("friend",      r"\b(friend|friends|mate|pal|buddy)\b"),
    ("where_work",  r"\b(where|planet express|delivery)\b.{0,20}\bwork\b|\bwork\b.{0,20}\b(where|company|place)\b"),
    ("where_live",  r"\b(where.{0,10}(live|from|come from)|where are you from)\b(?!.*\b(light|lamp|on|off|heating)\b)"),
    ("are_you_real",r"\b(real|robot|machine|computer|artificial)\b"),
    ("can_talk",    r"\bhow (can|do) you talk\b|\bcan you (really |actually )?talk\b|\bcan you speak\b"),
    ("what_can_do", r"\bwhat can you do\b|\babilities\b|\bwhat are you capable\b"),
    ("age",         r"\b(how old|age|born|year|built)\b"),
    ("job",         r"\b(job|purpose|programmed|function)\b|\bwhat do you do\b"),
]

TIME_PATTERNS = [
    r"\b(what time|what's the time|tell me the time|current time|time is it)\b",
    r"\b(what hour|how late is it)\b",
]

CONTEXTUAL_PATTERNS = [
    ("date", r"\b(what('s| is) the date|what day is it|today's date|current date)\b"),
    ("date", r"\b(what month|what year is it)\b"),
    ("weather_detail", r"\b(how (hot|cold|warm|chilly) is it)\b"),
    ("weather_detail", r"\b(is it (raining|snowing|sunny|cloudy|windy))\b"),
    ("weather_detail", r"\b(what('s| is) the temperature|how many degrees)\b"),
    ("status", r"\b(how are you (doing|running)|how('s| is) it going)\b"),
    ("status", r"\b(system status|your status|health check|you ok|are you ok)\b"),
]



# Location words to strip from extracted place names
_WEATHER_NOISE = re.compile(
    r"(what'?s?|what is|how'?s?|how is|tell me|give me|is it|will it|"
    r"the|weather|forecast|temperature|rain|raining|like|today|tomorrow|"
    r"right now|now|currently|outside|there|please|bender)",
    re.IGNORECASE,
)

_CITY_TIMEZONES: dict[str, str] = {
    "new york": "America/New_York", "new york city": "America/New_York",
    "los angeles": "America/Los_Angeles", "la": "America/Los_Angeles",
    "chicago": "America/Chicago", "denver": "America/Denver",
    "toronto": "America/Toronto", "vancouver": "America/Vancouver",
    "london": "Europe/London", "paris": "Europe/Paris",
    "berlin": "Europe/Berlin", "amsterdam": "Europe/Amsterdam",
    "madrid": "Europe/Madrid", "rome": "Europe/Rome",
    "athens": "Europe/Athens", "moscow": "Europe/Moscow",
    "dubai": "Asia/Dubai",
    "india": "Asia/Kolkata", "delhi": "Asia/Kolkata", "mumbai": "Asia/Kolkata",
    "bangkok": "Asia/Bangkok", "singapore": "Asia/Singapore",
    "hong kong": "Asia/Hong_Kong",
    "beijing": "Asia/Shanghai", "shanghai": "Asia/Shanghai", "china": "Asia/Shanghai",
    "tokyo": "Asia/Tokyo", "japan": "Asia/Tokyo",
    "seoul": "Asia/Seoul", "korea": "Asia/Seoul",
    "sydney": "Australia/Sydney", "australia": "Australia/Sydney",
    "melbourne": "Australia/Melbourne",
    "auckland": "Pacific/Auckland", "new zealand": "Pacific/Auckland",
    "hawaii": "Pacific/Honolulu", "alaska": "America/Anchorage",
    "karachi": "Asia/Karachi", "pakistan": "Asia/Karachi",
}


def _extract_time_timezone(text: str) -> str | None:
    """Extract an IANA timezone from a time query (e.g. 'in Tokyo'), or None for local."""
    m = re.search(r"\bin\s+((?:[A-Za-z][A-Za-z\-\']*\s*){1,3})", text, re.IGNORECASE)
    if not m:
        return None
    raw = m.group(1).strip().rstrip("?.,!")
    key = raw.lower()
    if key in _CITY_TIMEZONES:
        return _CITY_TIMEZONES[key]
    # Partial match: "New York City" → try "new york"
    words = key.split()
    for n in range(len(words) - 1, 0, -1):
        partial = " ".join(words[:n])
        if partial in _CITY_TIMEZONES:
            return _CITY_TIMEZONES[partial]
    return None


def _extract_weather_location(text: str) -> str | None:
    """Extract an explicit location from a weather query, or None for local."""
    t = text.strip()
    _TRAILING_NOISE_RE = re.compile(
        r"\s+\b(?:today|tomorrow|now|right\s+now|currently|please|bender)\b.*$",
        re.IGNORECASE,
    )
    _SKIP_WORDS = {"the", "a", "an", "it", "there", "outside", "here"}
    # Find all standalone "in/for/at" followed by up to 3 words
    for m in re.finditer(r"\b(?:in|for|at)\b\s+((?:[A-Za-z][A-Za-z\-\']*\s*){1,3})", t, re.IGNORECASE):
        raw = m.group(1).strip()
        raw = _TRAILING_NOISE_RE.sub("", raw).strip()
        if len(raw) < 2:
            continue
        first_word = raw.split()[0].lower()
        if first_word == "the":
            # "in the Maldives" -> strip leading "the"
            raw = raw.split(" ", 1)[1].strip() if " " in raw else raw
            first_word = raw.split()[0].lower() if raw else ""
        if not raw or first_word in _SKIP_WORDS:
            continue
        cleaned = _WEATHER_NOISE.sub("", raw).strip()
        if not cleaned:
            continue
        return raw.title()
    return None

def _match_any(text: str, patterns: list[str]) -> bool:
    for p in patterns:
        if re.search(p, text, re.IGNORECASE):
            return True
    return False


_INDEX_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "speech", "responses", "index.json"
)
_promoted_cache: list | None = None

def _promoted_patterns() -> list:
    """Load promoted patterns from index.json, cached in memory."""
    global _promoted_cache
    if _promoted_cache is None:
        try:
            with open(_INDEX_PATH) as f:
                _promoted_cache = json.load(f).get("promoted", [])
        except Exception:
            _promoted_cache = []
    return _promoted_cache

def reload_promoted():
    """Call after running prebuild_responses.py to pick up new promotions."""
    global _promoted_cache
    _promoted_cache = None


def _check_all_intents(t: str) -> list[str]:
    """Return all intents that match text (for multi-match diagnostics)."""
    matched = []
    if _match_any(t, HA_CONTROL_PATTERNS):
        matched.append("HA_CONTROL")
    if _match_any(t, HA_STATUS_PATTERNS) or (
        _match_any(t, HA_CONTROL_PATTERNS) and is_ha_question_or_narration(t)
    ):
        matched.append("HA_STATUS")
    if _match_any(t, TIMER_PATTERNS):
        matched.append("TIMER")
    if _match_any(t, TIMER_CANCEL_PATTERNS):
        matched.append("TIMER_CANCEL")
    if _match_any(t, TIMER_STATUS_PATTERNS):
        matched.append("TIMER_STATUS")
    if _match_any(t, WEATHER_PATTERNS):
        matched.append("WEATHER")
    if _match_any(t, NEWS_PATTERNS):
        matched.append("NEWS")
    if _match_any(t, DISMISSAL_PATTERNS):
        matched.append("DISMISSAL")
    if _match_any(t, JOKE_PATTERNS):
        matched.append("JOKE")
    if _match_any(t, VISION_PATTERNS):
        matched.append("VISION")
    if _match_any(t, GREETING_PATTERNS):
        matched.append("GREETING")
    if _match_any(t, AFFIRMATION_PATTERNS):
        matched.append("AFFIRMATION")
    for sub_key, pattern in PERSONAL_PATTERNS:
        if re.search(pattern, t, re.IGNORECASE):
            matched.append(f"PERSONAL/{sub_key}")
    return matched


def classify(text: str) -> tuple[str, str | None]:
    """
    Classify user text into an intent.
    Returns (intent_name, sub_key_or_None).
    """
    t = text.strip().lower()
    word_count = len(t.split())

    # Most specific first
    if _match_any(t, TIMER_PATTERNS):
        return ("TIMER", None)
    if _match_any(t, TIMER_CANCEL_PATTERNS):
        return ("TIMER_CANCEL", None)
    if _match_any(t, TIMER_STATUS_PATTERNS):
        return ("TIMER_STATUS", None)
    if _match_any(t, WEATHER_PATTERNS):
        location = _extract_weather_location(text.strip())
        return ("WEATHER", location)
    if _match_any(t, NEWS_PATTERNS):
        return ("NEWS", None)
    if _match_any(t, TIME_PATTERNS):
        timezone = _extract_time_timezone(text.strip())
        return ("TIME", timezone)

    # DISMISSAL is a session-control intent and must win over HA_CONTROL —
    # "bender, stop" must never be swallowed by a light/radiator pattern.
    # (TIMER_CANCEL is checked above, so "stop the timer" isn't shadowed by
    # DISMISSAL's bare \bstop\b pattern.)
    if _match_any(t, DISMISSAL_PATTERNS):
        return ("DISMISSAL", None)

    # HA_CONTROL vs HA_STATUS — a question/narration about lights or
    # radiators ("is the office light on", "the lights in the kitchen have
    # been turned on") must never fire a real HA write call. Only genuine
    # imperatives reach HA_CONTROL; everything else HA-shaped is read-only.
    ha_related = _match_any(t, HA_CONTROL_PATTERNS)
    ha_question = ha_related and is_ha_question_or_narration(t)
    if ha_related and not ha_question:
        return ("HA_CONTROL", None)
    if ha_question or _match_any(t, HA_STATUS_PATTERNS):
        return ("HA_STATUS", None)

    if _match_any(t, JOKE_PATTERNS):
        return ("JOKE", None)
    if _match_any(t, VISION_PATTERNS):
        return ("VISION", None)

    # CONTEXTUAL (before PERSONAL — more specific patterns)
    for sub_key, pattern in CONTEXTUAL_PATTERNS:
        if re.search(pattern, t, re.IGNORECASE):
            log.debug("Intent: CONTEXTUAL/%s", sub_key)
            return "CONTEXTUAL", sub_key

    # Personal questions — check sub-patterns
    for sub_key, pattern in PERSONAL_PATTERNS:
        if re.search(pattern, t, re.IGNORECASE):
            return ("PERSONAL", sub_key)

    # Promoted responses — check before falling through to AI
    for entry in _promoted_patterns():
        if re.search(entry["pattern"], t, re.IGNORECASE):
            return ("PROMOTED", entry["file"])

    # Vague catch-all intents — only match on short utterances
    if word_count <= cfg.simple_intent_max_words:
        if _match_any(t, GREETING_PATTERNS):
            return ("GREETING", None)
        if _match_any(t, AFFIRMATION_PATTERNS):
            return ("AFFIRMATION", None)
    else:
        # Log long utterances that would have matched simple intents
        for name, patterns in [("GREETING", GREETING_PATTERNS), ("AFFIRMATION", AFFIRMATION_PATTERNS)]:
            if _match_any(t, patterns):
                log.info("Long utterance (%d words) would match %s, falling through to UNKNOWN", word_count, name)

    # Multi-match diagnostic
    all_matches = _check_all_intents(t)
    if len(all_matches) > 1:
        log.warning("Multi-match: %s for %r", all_matches, t)
        metrics.count("intent_multi_match", resolved="UNKNOWN", others=str(all_matches))

    return ("UNKNOWN", None)


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        "Hello there",
        "Thanks a lot",
        "Goodbye Bender",
        "Tell me a joke",
        "What's the weather like?",
        "What's the weather like in Leeds today?",
        "What's the weather like in Portugal right now?",
        "Is it raining in Paris?",
        "The lights in the kitchen have been turned on",
        "Is the office light on?",
        "Are any lights on?",
        "What's the office temperature?",
        "Turn on the office lights",
        "Bender, stop",
        "How old are you?",
        "What do you eat?",
        "Do you like me?",
        "What's your job?",
        "Where do you live?",
        "Are you a robot?",
        "What is the meaning of life?",
        "Can you play some music?",
        "What do you see?",
        "Who's in the room?",
        "What time is it?",
        "What time is it in Tokyo?",
        "What time is it in New York?",
        "What's the weather like in Paris?",
        "What's the weather in Sydney today?",
    ]
    for t in tests:
        intent, sub = classify(t)
        print(f"  {t!r:50s} → {intent}" + (f" / {sub}" if sub else ""))
