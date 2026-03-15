#!/usr/bin/env python3
"""
Weather handler — fetches from Home Assistant weather entity
and produces a Bender-style spoken response.

Called from wake_converse.py when WEATHER intent is detected.
Returns a WAV path (temp file) for the response.
"""

import os
import random
import sys
import urllib.request
import urllib.error
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tts_generate

HA_URL    = os.environ.get("HA_URL",   "http://192.168.68.125:8123")
HA_TOKEN  = os.environ.get("HA_TOKEN", "")
ENTITY_ID = os.environ.get("HA_WEATHER_ENTITY", "weather.forecast_home")

# Bender-style weather commentary — filled with {conditions} and/or {temp}
TEMPLATES = [
    "It's {temp} degrees and {condition} in Nottingham. "
    "High of {high} today. "
    "In other words, classic miserable British weather. You're all doomed.",

    "Current conditions: {temp} degrees, {condition}. "
    "Forecast high: {high}. "
    "Don't blame me if you melt. Or freeze. Either way, not my problem.",

    "It is {temp} degrees outside. {condition_cap}. "
    "My sensors tell me the high will be {high}. "
    "My opinion is that you should stay inside and pour me a drink.",

    "Outside it's {temp} degrees and {condition}. "
    "Later it'll get up to {high}. "
    "Thrilling. I'll be here, inside, being superior.",
]


def _fetch_state(entity_id: str) -> dict:
    req = urllib.request.Request(
        f"{HA_URL}/api/states/{entity_id}",
        headers={
            "Authorization": f"Bearer {HA_TOKEN}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read())


def _format_condition(raw: str) -> str:
    """Convert HA condition slug to plain English."""
    mapping = {
        "sunny": "sunny",
        "partlycloudy": "partly cloudy",
        "cloudy": "cloudy",
        "rainy": "raining",
        "snowy": "snowing",
        "fog": "foggy",
        "windy": "windy",
        "hail": "hailing",
        "lightning": "stormy",
        "lightning-rainy": "stormy with rain",
        "snowy-rainy": "sleeting",
        "clear-night": "clear",
        "overcast": "overcast",
    }
    return mapping.get(raw.lower(), raw.replace("-", " ").replace("_", " "))


def get_weather_response() -> str:
    """Return a WAV path with Bender's weather update."""
    try:
        state = _fetch_state(ENTITY_ID)
        condition = _format_condition(state.get("state", "unknown"))
        attrs = state.get("attributes", {})
        temp = round(attrs.get("temperature", 0))
        # Try to get today's high from forecast
        forecast = attrs.get("forecast", [])
        high = round(forecast[0].get("temperature", temp)) if forecast else temp
    except Exception as e:
        text = "My weather sensors are down. Or I just don't care. Probably both."
        return tts_generate.speak(text)

    template = random.choice(TEMPLATES)
    text = template.format(
        temp=temp,
        condition=condition,
        condition_cap=condition.capitalize(),
        high=high,
    )
    return tts_generate.speak(text)


if __name__ == "__main__":
    import subprocess
    wav = get_weather_response()
    print(f"Generated: {wav}")
    subprocess.run(["aplay", wav])
    os.unlink(wav)
