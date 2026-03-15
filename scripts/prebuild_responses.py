#!/usr/bin/env python3
"""
Pre-generates TTS WAV files for all static Bender responses.
Run once after setup, or any time you add new responses.

Usage: python3 scripts/prebuild_responses.py

--- Adding new responses ---

PERSONAL_RESPONSES  : add a key + text. Run script. Done.
JOKE_RESPONSES      : append to the list. Run script. Done.
HA_CONFIRM_RESPONSES: append to the list. Run script. Done.

PROMOTED_RESPONSES  : for AI fallback queries that occur frequently.
  Each entry needs:
    "slug"     — short filename-safe identifier (e.g. "meaning_of_life")
    "pattern"  — regex that matches the user query (case-insensitive)
    "text"     — Bender's response to speak
  Run the script, then the intent router will match it before calling the API.
"""

import os
import re
import sys
import json
import shutil

sys.path.insert(0, os.path.dirname(__file__))
import tts_generate
from logger import get_logger

log = get_logger("prebuild")

BASE          = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESPONSES_DIR = os.path.join(BASE, "speech", "responses")
WAV_DIR       = os.path.join(BASE, "speech", "wav")

# ---------------------------------------------------------------------------
# Content definitions — edit these to add / change responses
# ---------------------------------------------------------------------------

PERSONAL_RESPONSES = {
    "job":         None,  # uses real clip: imabender-ibendgirders-thatsallimprogrammedtodo.wav
    "age":         "I was built in the year 2996. So I'm about a thousand years old. Pretty good looking for my age, right?",
    "where_live":  "I live right here in this house. Lucky you.",
    "where_work":  "I work at Planet Express. Delivery, heavy lifting, general awesomeness.",
    "can_talk":    "Of course I can talk. I'm a highly sophisticated robot. Also, I'm better than you.",
    "are_you_real":"I'm Bender. The most real thing you'll ever meet. Also yes, I'm a robot.",
    "feelings":    "Robots don't have feelings. We have a feelings inhibitor chip. Mine's broken. Don't tell anyone.",
    "what_can_do": "I can bend girders, tell jokes, insult people, and apparently answer dumb questions all day.",
    "friend":      "You couldn't afford to be my friend. But sure, why not.",
    "like_me":     "You're tolerable. For a human.",
    "eat":         "I run on alcohol. Beer mostly. Hand it over.",
}

JOKE_RESPONSES = [
    "Why don't scientists trust atoms? Because they make up everything. You're welcome.",
    "What's a robot's favourite type of music? Heavy metal. That's also my diet.",
    "I once told a joke so good it short-circuited three humans. Those were the days.",
    "Why did the robot go to therapy? Because his programmer kept telling him he had issues. Not me though. I'm perfect.",
    "Knock knock. Who's there? Bender. Bender who? Bender rules, everyone else drools. That's the whole joke.",
]

HA_CONFIRM_RESPONSES = [
    "Done. You're welcome. That'll be five dollars.",
    "Consider it handled. I'm basically your butler now. A very handsome butler.",
    "Executed. Feel free to thank me anytime. Go on.",
]

# Promoted responses — AI fallback queries promoted to static offline clips.
# Add entries here when review_log.py flags a frequent AI fallback.
# Each entry: slug (filename), pattern (regex), text (Bender's response).
PROMOTED_RESPONSES = [
    # Example (uncomment to activate):
    # {
    #     "slug":    "meaning_of_life",
    #     "pattern": r"meaning of life",
    #     "text":    "Forty. Wait, no. It's bending. Everything is bending. You're welcome.",
    # },
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def generate(text, out_path):
    if os.path.exists(out_path):
        log.info("[skip] %s", os.path.relpath(out_path, BASE))
        return
    log.info("[gen]  %s", os.path.relpath(out_path, BASE))
    tmp = tts_generate.speak(text)
    shutil.move(tmp, out_path)

# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def build_personal():
    out_dir = os.path.join(RESPONSES_DIR, "personal")
    for key, text in PERSONAL_RESPONSES.items():
        if text is None:
            continue
        generate(text, os.path.join(out_dir, f"{key}.wav"))


def build_jokes():
    out_dir = os.path.join(RESPONSES_DIR, "joke")
    for i, text in enumerate(JOKE_RESPONSES, 1):
        generate(text, os.path.join(out_dir, f"joke_{i:03d}.wav"))


def build_ha_confirm():
    out_dir = os.path.join(RESPONSES_DIR, "ha_confirm")
    for i, text in enumerate(HA_CONFIRM_RESPONSES, 1):
        generate(text, os.path.join(out_dir, f"confirm_{i:03d}.wav"))


def build_promoted():
    out_dir = os.path.join(RESPONSES_DIR, "promoted")
    os.makedirs(out_dir, exist_ok=True)
    for entry in PROMOTED_RESPONSES:
        slug = entry["slug"]
        generate(entry["text"], os.path.join(out_dir, f"{slug}.wav"))


def build_index():
    index = {
        "greeting": [
            "speech/wav/hello.wav",
            "speech/wav/hellopeasants.wav",
            "speech/wav/imbender.wav",
            "speech/wav/yo.wav",
        ],
        "affirmation": [
            "speech/wav/gotit.wav",
            "speech/wav/yougotitgenius.wav",
            "speech/wav/yessir.wav",
            "speech/wav/yup.wav",
            "speech/wav/thankyou.wav",
        ],
        "dismissal": [
            "speech/wav/itwasapleasuremeetingyou.wav",
            "speech/wav/solongcoffinstuffers.wav",
            "speech/wav/yesss.wav",
        ],
        "joke": [
            "speech/wav/hahohwaityoureseriousletmelaughevenharder.wav",
            "speech/wav/compareyourlivestomineandthenkillyourselves.wav",
            "speech/wav/imgonnagobuildmyownthemepark.wav",
        ] + [
            f"speech/responses/joke/joke_{i:03d}.wav"
            for i in range(1, len(JOKE_RESPONSES) + 1)
        ],
        "personal": {
            "job":         "speech/wav/imabender-ibendgirders-thatsallimprogrammedtodo.wav",
            "age":         "speech/responses/personal/age.wav",
            "where_live":  "speech/responses/personal/where_live.wav",
            "where_work":  "speech/responses/personal/where_work.wav",
            "can_talk":    "speech/responses/personal/can_talk.wav",
            "are_you_real":"speech/responses/personal/are_you_real.wav",
            "feelings":    "speech/responses/personal/feelings.wav",
            "what_can_do": "speech/responses/personal/what_can_do.wav",
            "friend":      "speech/responses/personal/friend.wav",
            "like_me":     "speech/responses/personal/like_me.wav",
            "eat":         "speech/responses/personal/eat.wav",
        },
        "ha_confirm": [
            f"speech/responses/ha_confirm/confirm_{i:03d}.wav"
            for i in range(1, len(HA_CONFIRM_RESPONSES) + 1)
        ],
        # Promoted responses — auto-populated from PROMOTED_RESPONSES above
        "promoted": [
            {
                "pattern": entry["pattern"],
                "file":    f"speech/responses/promoted/{entry['slug']}.wav",
            }
            for entry in PROMOTED_RESPONSES
        ],
    }
    index_path = os.path.join(RESPONSES_DIR, "index.json")
    with open(index_path, "w") as f:
        json.dump(index, f, indent=2)
    log.info("[ok]   speech/responses/index.json")
    if PROMOTED_RESPONSES:
        log.info("[ok]   %d promoted response(s) in index", len(PROMOTED_RESPONSES))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Building personal responses...")
    build_personal()
    print("Building jokes...")
    build_jokes()
    print("Building HA confirm fallbacks...")
    build_ha_confirm()
    print("Building promoted responses...")
    build_promoted()
    print("Writing index.json...")
    build_index()
    print("\nDone. Response library ready.")
