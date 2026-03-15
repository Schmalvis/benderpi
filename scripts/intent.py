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
  HA_CONFIRM    — HA status confirmation messages
  UNKNOWN       — everything else → AI fallback
"""

import json
import os
import re

# ---------------------------------------------------------------------------
# Pattern sets — order matters (more specific first within PERSONAL)
# ---------------------------------------------------------------------------

GREETING_PATTERNS = [
    r"\b(hello|hi|hey|howdy)\b",
    r"\bhow are you\b",
    r"\byou there\b",
    r"\bwake up\b",
    r"\byo\b",
]

AFFIRMATION_PATTERNS = [
    r"\bthank(s| you)\b",
    r"\bgreat\b",
    r"\bnice one\b",
    r"\bgood\b",
    r"\bok(ay)?\b",
    r"\bbrilliant\b",
    r"\bawesome\b",
    r"\bcheers\b",
]

DISMISSAL_PATTERNS = [
    r"\bbye\b",
    r"\bgoodbye\b",
    r"\bsee you\b",
    r"\bstop\b",
    r"\bthat'?s? all\b",
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
    r"\bgoing to rain\b",
]

HA_CONFIRM_PATTERNS = [
    r"\b(turned|switching|switched|turn) (on|off)\b",
    r"\blight(s)? (on|off|are)\b",
    r"\b(heating|thermostat|temperature) (set|is|to)\b",
    r"\bset to \d+\b",
    r"\bi('?ve| have) (turned|set|switched)\b",
]

# Personal question sub-keys and their trigger patterns
PERSONAL_PATTERNS = [
    ("eat",         r"\b(eat|food|drink|hungry|alcohol|beer|fuel|power)\b"),
    ("feelings",    r"\b(feel(ings?)?|emotion(al)?|happy|sad|angry|upset|care)\b"),
    ("like_me",     r"\bdo you like me\b|\byou like me\b"),
    ("friend",      r"\b(friend|friends|mate|pal|buddy)\b"),
    ("where_work",  r"\b(where|planet express|delivery)\b.{0,20}\bwork\b|\bwork\b.{0,20}\b(where|company|place)\b"),
    ("where_live",  r"\b(where|where'?re? you|come from|home|house|live)\b"),
    ("are_you_real",r"\b(real|robot|machine|computer|artificial)\b"),
    ("can_talk",    r"\bhow (can|do) you talk\b|\bcan you (really |actually )?talk\b|\bcan you speak\b"),
    ("what_can_do", r"\bwhat can you do\b|\babilities\b|\bwhat are you capable\b"),
    ("age",         r"\b(how old|age|born|year|built)\b"),
    ("job",         r"\b(job|work|do|programmed|purpose|function)\b"),
]


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


def classify(text: str) -> tuple[str, str | None]:
    """
    Classify user text into an intent.
    Returns (intent_name, sub_key_or_None).
    """
    t = text.strip().lower()

    # HA confirm is often a statement, not a question — check early
    if _match_any(t, HA_CONFIRM_PATTERNS):
        return ("HA_CONFIRM", None)

    if _match_any(t, GREETING_PATTERNS):
        return ("GREETING", None)

    if _match_any(t, DISMISSAL_PATTERNS):
        return ("DISMISSAL", None)

    if _match_any(t, AFFIRMATION_PATTERNS):
        return ("AFFIRMATION", None)

    if _match_any(t, JOKE_PATTERNS):
        return ("JOKE", None)

    if _match_any(t, WEATHER_PATTERNS):
        return ("WEATHER", None)

    # Personal questions — check sub-patterns
    for sub_key, pattern in PERSONAL_PATTERNS:
        if re.search(pattern, t, re.IGNORECASE):
            return ("PERSONAL", sub_key)

    # Promoted responses — check before falling through to AI
    for entry in _promoted_patterns():
        if re.search(entry["pattern"], t, re.IGNORECASE):
            return ("PROMOTED", entry["file"])

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
