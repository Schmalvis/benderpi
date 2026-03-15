#!/usr/bin/env python3
"""
HA status handler — interprets Home Assistant confirmation messages
and generates a Bender-style TTS response.

The assumption is HA sends a plain-English string via the wake_converse loop,
e.g. "The kitchen lights have been turned on" or "Temperature is set to 21".
"""

import os
import re
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tts_generate

# (pattern, [response templates])  — first match wins
RULES = [
    (
        r"\b(lights?|lamp)\b.{0,30}\boff\b|\boff\b.{0,30}\b(lights?|lamp)\b",
        [
            "Lights off. {room}Darkness suits you.",
            "Going dark. {room}Very dramatic. I approve.",
            "Lights out. {room}At least now I can't see your face.",
        ],
    ),
    (
        r"\b(lights?|lamp)\b.{0,30}\bon\b|\bon\b.{0,30}\b(lights?|lamp)\b",
        [
            "Lights on. {room}You're welcome. That'll be five dollars.",
            "Let there be light. {room}Mostly for your benefit. I can see in the dark.",
            "Illuminated. {room}Try not to do anything embarrassing in the light.",
        ],
    ),
    (
        r"\b(temperature|thermostat|heating)\b.{0,30}\b(\d+)\b",
        [
            "{degrees} degrees. {room}Some of us run on alcohol, so we don't notice the cold.",
            "Set to {degrees}. {room}Very specific. Very human.",
            "{degrees} degrees it is. {room}Cosy. For a meat bag.",
        ],
    ),
    (
        r"\b(turn(ed)?|switch(ed)?)\b.{0,10}\boff\b",
        [
            "Switched off. {room}Done. I'm basically your butler now.",
            "Off. {room}Easy. I could do this in my sleep. If I slept.",
        ],
    ),
    (
        r"\b(turn(ed)?|switch(ed)?)\b.{0,10}\bon\b",
        [
            "Switched on. {room}Executed flawlessly, as always.",
            "On. {room}You're welcome. The applause can wait.",
        ],
    ),
]

FALLBACK_RESPONSES = [
    "Done. You're welcome.",
    "Handled. I'm on it.",
    "Executed. Feel free to be impressed.",
    "Consider it done. Again.",
]


def _extract_room(text: str) -> str:
    """Try to extract a room name from the text."""
    match = re.search(
        r"\bin the (\w[\w\s]+?)\b(?= have| has| is| are|\.|\,|$)",
        text, re.IGNORECASE
    )
    if match:
        return f"In the {match.group(1).strip().lower()}. "
    return ""


def _extract_degrees(text: str) -> str:
    match = re.search(r"\b(\d+)\b", text)
    return match.group(1) if match else "that temperature"


def get_ha_response(user_text: str) -> str:
    """Return a WAV path with Bender's HA confirmation response."""
    room = _extract_room(user_text)
    degrees = _extract_degrees(user_text)

    for pattern, templates in RULES:
        if re.search(pattern, user_text, re.IGNORECASE):
            template = random.choice(templates)
            text = template.format(room=room, degrees=degrees)
            return tts_generate.speak(text)

    text = random.choice(FALLBACK_RESPONSES)
    return tts_generate.speak(text)


if __name__ == "__main__":
    import subprocess
    tests = [
        "The kitchen lights have been turned on",
        "Lights off in the living room",
        "Temperature is set to 21 degrees in Martin's office",
        "Heating turned off",
    ]
    for t in tests:
        print(f"\nInput: {t}")
        wav = get_ha_response(t)
        subprocess.run(["aplay", wav])
        os.unlink(wav)
