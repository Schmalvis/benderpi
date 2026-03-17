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
  WEATHER       — weather/forecast/rain
  NEWS          — news/headlines/what's happening
  HA_CONTROL    — Commands to control HA devices (lights, switches)
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
    r"\bhow are you\b",
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
    r"^stop(\s+(it|bender))?$",
    r"\bthat'?s?\s*all\b",
    r"\bno more\b",
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
    r"\bis it raining\b",
    r"\bwill it rain\b",
    r"\btemperature outside\b",
    r"\b(what.{0,15}temperature|temperature in|how (hot|cold|warm))\b",
    r"\bgoing to rain\b",
]
NEWS_PATTERNS = [
    r"\bnews\b",
    r"\bheadlines?\b",
    r"\bwhat.{0,10}happening\b",
    r"\blatest updates?\b",
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

PERSONAL_PATTERNS = [
    ("eat",         r"\b(eat|food|drink|hungry|alcohol|beer|fuel|power)\b"),
    ("feelings",    r"\b(feel(ings?)?|emotion(al)?|happy|sad|angry|upset|care)\b"),
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



# Location words to strip from extracted place names
_WEATHER_NOISE = re.compile(
    r"(what'?s?|what is|how'?s?|how is|tell me|give me|is it|will it|"
    r"the|weather|forecast|temperature|rain|raining|like|today|tomorrow|"
    r"right now|now|currently|outside|there|please|bender)",
    re.IGNORECASE,
)

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
    if _match_any(t, WEATHER_PATTERNS):
        matched.append("WEATHER")
    if _match_any(t, NEWS_PATTERNS):
        matched.append("NEWS")
    if _match_any(t, DISMISSAL_PATTERNS):
        matched.append("DISMISSAL")
    if _match_any(t, JOKE_PATTERNS):
        matched.append("JOKE")
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
    if _match_any(t, HA_CONTROL_PATTERNS):
        return ("HA_CONTROL", None)
    if _match_any(t, WEATHER_PATTERNS):
        location = _extract_weather_location(text.strip())
        return ("WEATHER", location)
    if _match_any(t, NEWS_PATTERNS):
        return ("NEWS", None)
    if _match_any(t, DISMISSAL_PATTERNS):
        return ("DISMISSAL", None)
    if _match_any(t, JOKE_PATTERNS):
        return ("JOKE", None)

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
        "How old are you?",
        "What do you eat?",
        "Do you like me?",
        "What's your job?",
        "Where do you live?",
        "Are you a robot?",
        "What is the meaning of life?",
        "Can you play some music?",
    ]
    for t in tests:
        intent, sub = classify(t)
        print(f"  {t!r:50s} → {intent}" + (f" / {sub}" if sub else ""))
