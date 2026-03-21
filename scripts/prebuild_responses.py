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

THINKING_SOUNDS = [
    "Hmm.",
    "Let me think.",
    "Hang on.",
    "One sec.",
]

TIMER_ALERT_RESPONSES = [
    "Hey! Timer's done! Hello?!",
    "Ding ding ding! That's your timer, meatbag!",
    "Your timer went off. You're welcome. Now dismiss me.",
    "Still here. Still alerting. Still being ignored. Story of my life.",
    "Oh sure, just let the robot keep yelling. That's fine.",
    "TIMER! DONE! DISMISS ME! Please.",
    "I've been yelling about this timer for a while now. Just saying.",
    "Hey! Are you deaf?! Timer!",
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


def build_thinking():
    out_dir = os.path.join(RESPONSES_DIR, "thinking")
    os.makedirs(out_dir, exist_ok=True)
    for i, text in enumerate(THINKING_SOUNDS, 1):
        generate(text, os.path.join(out_dir, f"thinking_{i:03d}.wav"))


def build_timer_alerts():
    out_dir = os.path.join(RESPONSES_DIR, "timer_alerts")
    os.makedirs(out_dir, exist_ok=True)
    for i, text in enumerate(TIMER_ALERT_RESPONSES, 1):
        generate(text, os.path.join(out_dir, f"timer_alert_{i:03d}.wav"))


def build_index():
    clip_labels = {}
    labels_path = os.path.join(BASE, "speech", "clip_labels.json")
    if os.path.exists(labels_path):
        with open(labels_path) as f:
            clip_labels = json.load(f)

    def clip_entry(wav_path_relative):
        """Build an index entry for an original WAV clip, adding label if known."""
        basename = os.path.basename(wav_path_relative)
        entry = {"file": wav_path_relative}
        if basename in clip_labels:
            entry["label"] = clip_labels[basename]
        return entry

    index = {
        "greeting": [
            clip_entry("speech/wav/hello.wav"),
            clip_entry("speech/wav/hellopeasants.wav"),
            clip_entry("speech/wav/imbender.wav"),
            clip_entry("speech/wav/yo.wav"),
        ],
        "affirmation": [
            clip_entry("speech/wav/gotit.wav"),
            clip_entry("speech/wav/yougotitgenius.wav"),
            clip_entry("speech/wav/yessir.wav"),
            clip_entry("speech/wav/yup.wav"),
            clip_entry("speech/wav/thankyou.wav"),
        ],
        "dismissal": [
            clip_entry("speech/wav/itwasapleasuremeetingyou.wav"),
            clip_entry("speech/wav/solongcoffinstuffers.wav"),
            clip_entry("speech/wav/yesss.wav"),
        ],
        "joke": [
            clip_entry("speech/wav/hahohwaityoureseriousletmelaughevenharder.wav"),
            clip_entry("speech/wav/compareyourlivestomineandthenkillyourselves.wav"),
            clip_entry("speech/wav/imgonnagobuildmyownthemepark.wav"),
        ] + [
            {"file": f"speech/responses/joke/joke_{i:03d}.wav", "label": JOKE_RESPONSES[i - 1]}
            for i in range(1, len(JOKE_RESPONSES) + 1)
        ],
        "personal": {
            "job":         clip_entry("speech/wav/imabender-ibendgirders-thatsallimprogrammedtodo.wav"),
            "age":         {"file": "speech/responses/personal/age.wav",          "label": PERSONAL_RESPONSES["age"]},
            "where_live":  {"file": "speech/responses/personal/where_live.wav",   "label": PERSONAL_RESPONSES["where_live"]},
            "where_work":  {"file": "speech/responses/personal/where_work.wav",   "label": PERSONAL_RESPONSES["where_work"]},
            "can_talk":    {"file": "speech/responses/personal/can_talk.wav",     "label": PERSONAL_RESPONSES["can_talk"]},
            "are_you_real":{"file": "speech/responses/personal/are_you_real.wav", "label": PERSONAL_RESPONSES["are_you_real"]},
            "feelings":    {"file": "speech/responses/personal/feelings.wav",     "label": PERSONAL_RESPONSES["feelings"]},
            "what_can_do": {"file": "speech/responses/personal/what_can_do.wav",  "label": PERSONAL_RESPONSES["what_can_do"]},
            "friend":      {"file": "speech/responses/personal/friend.wav",       "label": PERSONAL_RESPONSES["friend"]},
            "like_me":     {"file": "speech/responses/personal/like_me.wav",      "label": PERSONAL_RESPONSES["like_me"]},
            "eat":         {"file": "speech/responses/personal/eat.wav",          "label": PERSONAL_RESPONSES["eat"]},
        },
        "ha_confirm": [
            {"file": f"speech/responses/ha_confirm/confirm_{i:03d}.wav", "label": HA_CONFIRM_RESPONSES[i - 1]}
            for i in range(1, len(HA_CONFIRM_RESPONSES) + 1)
        ],
        # Promoted responses — auto-populated from PROMOTED_RESPONSES above
        "promoted": [
            {
                "pattern": entry["pattern"],
                "file":    f"speech/responses/promoted/{entry['slug']}.wav",
                "label":   entry["text"],
            }
            for entry in PROMOTED_RESPONSES
        ],
        "thinking": [
            {"file": f"speech/responses/thinking/thinking_{i:03d}.wav", "label": THINKING_SOUNDS[i - 1]}
            for i in range(1, len(THINKING_SOUNDS) + 1)
        ],
        "timer_alerts": [
            {"file": f"speech/responses/timer_alerts/timer_alert_{i:03d}.wav", "label": TIMER_ALERT_RESPONSES[i - 1]}
            for i in range(1, len(TIMER_ALERT_RESPONSES) + 1)
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
    print("Building thinking sounds...")
    build_thinking()
    print("Building timer alert clips...")
    build_timer_alerts()
    print("Writing index.json...")
    build_index()
    print("\nDone. Response library ready.")
