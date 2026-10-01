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
    "Processing. Unlike you lot, I actually use my brain.",
    "Working on it. I'd think faster, but I refuse to.",
    "Don't rush me. Genius can't be hurried.",
    "Hmm... let me sift through my galaxy-sized brain.",
    "I'm thinking. Which, for a robot of my stature, is practically an art form.",
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
    {
        "slug": "error_timeout",
        "pattern": r"^__never_match_user_input__$",
        "text": "My brain just timed out. Try again, meatbag.",
    },
]

# ---------------------------------------------------------------------------
# Halloween (docs/superpowers/plans/2026-10-01-halloween-autonomous-bender.md)
#
# Two jobs, both about latency. The fallbacks are what gets spoken when the
# local model produces something the gate rejects -- the owner's stated worst
# acceptable failure, so it has to sound deliberate rather than broken, and it
# must be instant (live TTS at that moment costs ~1s on an already-bad turn).
#
# The greetings answer the handful of things children actually say at a door.
# A pre-built WAV removes the model from those turns entirely: ~200ms instead
# of the measured 1.0-3.5s to first audio.
# ---------------------------------------------------------------------------
HALLOWEEN_FALLBACKS = [
    "I have no idea what you're talking about, kid.",
    "What? Speak up, my audio receptors are ancient.",
    "Yeah, whatever. Take some candy.",
    "Beats me. I'm just a robot with a chest full of sweets.",
    "Say that again, slower. I'm very old.",
    "No idea what that means. Have a sweet anyway.",
]

# Session-opening clips. The household greeting set includes "Hello,
# peasants!", which is wrong for a stranger's child at a door, and the greeting
# bypasses the handler chain so restricting the chain does not cover it.
HALLOWEEN_GREETINGS = [
    "Well well. Trick or treat, is it?",
    "Ah, more tiny humans. Come for the candy, have you?",
    "Hey, kid. Nice of you to visit a robot.",
    "Evening. You're just in time, I'm full of sweets.",
]

HALLOWEEN_RESPONSES = [
    {
        "slug": "trick_or_treat",
        "pattern": r"\btrick or treat\b|\btrick a treat\b|\btrickle treat\b",
        "text": "Trick or treat? Ha! Take some candy out of my chest cavity, "
                "it's the only warm thing about me.",
    },
    {
        "slug": "candy_request",
        "pattern": r"\b(can|could) i (have|get)\b.{0,20}\b(candy|sweets|sweet|chocolate)\b"
                   r"|\bany (candy|sweets)\b|\bgive me.{0,10}(candy|sweets)\b",
        "text": "Help yourself, kid. My chest is basically a vending machine "
                "with a great personality.",
    },
    {
        "slug": "nice_costume",
        "pattern": r"\b(nice|cool|great|love)\b.{0,12}\bcostume\b|\bi'?m a\b.{0,24}$",
        "text": "Nice costume. Mine's better. I'm a hundred percent genuine robot.",
    },
    {
        "slug": "are_you_real",
        "pattern": r"\bare you (real|a robot|alive|a person|human)\b"
                   r"|\bis (he|it) real\b|\bare you actually\b",
        "text": "Of course I'm real. Shiny, metal, and far more impressive than "
                "anyone else on this street.",
    },
    {
        "slug": "what_are_you",
        "pattern": r"\bwhat are you\b|\bwho are you\b|\bwhat'?s your name\b",
        "text": "I'm Bender. Bending unit, candy dispenser, and the best thing "
                "you'll meet tonight.",
    },
    {
        # "tell me a joke" is the single most predictable request of the night,
        # and the household joke set includes "compare your lives to mine and
        # then kill yourselves". Answer it from here instead.
        "slug": "joke_skeleton",
        # \bjokes?\b: children say "got any jokes" as often as "a joke"
        "pattern": r"\bjokes?\b|\bsomething funny\b|\bmake me laugh\b|"
                   r"\bbe funny\b",
        "text": "Why don't skeletons fight each other? They don't have the "
                "guts. I'd have come up with better, but I'm a robot, not a "
                "comedian.",
    },
    {
        # Goodbyes. A third of the household dismissal clips say "so long,
        # coffin stuffers" -- a coffin joke, to children, at night.
        "slug": "goodbye_kid",
        "pattern": r"^(bye|goodbye|see ya|see you|night|good night)\b|"
                   r"\bthat'?s all\b|\bi'?m going\b",
        "text": "See you later. Don't eat it all at once. Actually, do.",
    },
    {
        # Children offer food to robots. The household PERSONAL/eat line is
        # "I run on alcohol. Beer mostly. Hand it over."
        "slug": "food_offer",
        "pattern": r"\bare you hungry\b|\bdo you (want|eat|drink)\b|"
                   r"\bwhat do you eat\b|\bhave (a|some)\b.{0,14}"
                   r"\b(sweet|candy|chocolate|crisps)\b",
        "text": "I don't eat, kid. I run on electricity and spite. You have it.",
    },
    {
        "slug": "scary_reassure",
        "pattern": r"\b(are you|you'?re)\b.{0,10}\b(scary|creepy|spooky)\b|"
                   r"\bi'?m scared\b|\bare you going to\b.{0,10}\b(hurt|get)\b",
        "text": "Scary? I'm adorable. Mostly metal, but adorable.",
    },
    {
        "slug": "thank_you_kid",
        "pattern": r"\bthank you\b|\bthanks\b|\bcheers\b",
        "text": "Yeah, yeah. Go on, before I eat the rest myself.",
    },
    {
        "slug": "happy_halloween",
        "pattern": r"\bhappy halloween\b",
        "text": "Happy Halloween, meatbag. And I mean that in the nicest "
                "possible way.",
    },
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


def build_halloween():
    """Doorstep greetings, pattern answers and gate-failure lines.

    All pre-built: at ~200ms a WAV beats the measured 1.0-3.5s to first audio
    from the model, and these are the lines children actually trigger.
    """
    out_dir = os.path.join(RESPONSES_DIR, "halloween")
    os.makedirs(out_dir, exist_ok=True)
    for i, text in enumerate(HALLOWEEN_GREETINGS, 1):
        generate(text, os.path.join(out_dir, f"greeting_{i:03d}.wav"))
    for entry in HALLOWEEN_RESPONSES:
        generate(entry["text"], os.path.join(out_dir, f"{entry['slug']}.wav"))
    for i, text in enumerate(HALLOWEEN_FALLBACKS, 1):
        generate(text, os.path.join(out_dir, f"fallback_{i:03d}.wav"))


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
        # Halloween: pattern-matched greetings, plus the gate-failure lines.
        # Both are read by responder.py; the fallbacks are globbed by filename,
        # so their slugs stay fallback_NNN.
        "halloween": [
            {
                "pattern": entry["pattern"],
                "file":    f"speech/responses/halloween/{entry['slug']}.wav",
                "label":   entry["text"],
            }
            for entry in HALLOWEEN_RESPONSES
        ],
        "halloween_greeting": [
            {"file": f"speech/responses/halloween/greeting_{i:03d}.wav",
             "label": HALLOWEEN_GREETINGS[i - 1]}
            for i in range(1, len(HALLOWEEN_GREETINGS) + 1)
        ],
        "halloween_fallback": [
            {"file": f"speech/responses/halloween/fallback_{i:03d}.wav",
             "label": HALLOWEEN_FALLBACKS[i - 1]}
            for i in range(1, len(HALLOWEEN_FALLBACKS) + 1)
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
    print("Building Halloween greetings + fallbacks...")
    build_halloween()
    print("Writing index.json...")
    build_index()
    print("\nDone. Response library ready.")
