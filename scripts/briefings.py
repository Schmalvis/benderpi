#!/usr/bin/env python3
"""
briefings.py — Pre-generated TTS briefings for weather and news.

Briefings are cached as WAV files and refreshed when stale:
  weather: every 30 minutes
  news:    every 2 hours

Usage:
    from briefings import get_weather_wav, get_news_wav
    wav_path = get_weather_wav()   # caller must NOT delete (it's a cache file)
    wav_path = get_news_wav()
"""

import os
import re
import sys
import json
import random
import time
import shutil
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tts_generate

BASE_DIR     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAILY_DIR    = os.path.join(BASE_DIR, "speech", "responses", "daily")
WEATHER_WAV  = os.path.join(DAILY_DIR, "weather_briefing.wav")
NEWS_WAV     = os.path.join(DAILY_DIR, "news_briefing.wav")
META_PATH    = os.path.join(DAILY_DIR, "briefings_meta.json")

WEATHER_TTL  = 30 * 60    # 30 minutes
NEWS_TTL     = 2 * 60 * 60  # 2 hours

HA_URL_DEFAULT    = "http://192.168.68.125:8123"
HA_TOKEN_DEFAULT  = ""
HA_ENTITY_DEFAULT = "weather.forecast_home"

NEWS_FEEDS = [
    ("UK",      "https://feeds.bbci.co.uk/news/uk/rss.xml",      2),
    ("England", "https://feeds.bbci.co.uk/news/england/rss.xml", 2),
]

os.makedirs(DAILY_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# Metadata (timestamps of last successful generation)
# ---------------------------------------------------------------------------

def _load_meta() -> dict:
    try:
        with open(META_PATH) as f:
            return json.load(f)
    except Exception:
        return {}

def _save_meta(meta: dict):
    with open(META_PATH, "w") as f:
        json.dump(meta, f)

def _is_fresh(key: str, ttl: int) -> bool:
    meta = _load_meta()
    last = meta.get(key, 0)
    return (time.time() - last) < ttl

def _mark_fresh(key: str):
    meta = _load_meta()
    meta[key] = time.time()
    _save_meta(meta)

# ---------------------------------------------------------------------------
# Weather briefing
# ---------------------------------------------------------------------------

WEATHER_TEMPLATES = [
    "Right, weather. Currently {temp} degrees and {condition}. "
    "Today's high: {high}. Humidity {humidity} percent. "
    "{wind_line} "
    "{comment}",

    "You asked about weather, so here it is. {temp} degrees, {condition}. "
    "High of {high} today. "
    "{wind_line} "
    "{comment}",
]

WEATHER_COMMENTS = {
    "sunny":        "Sunny. Disgusting. I preferred it when everything was grey.",
    "partlycloudy": "Partly cloudy. Make your mind up, sky.",
    "cloudy":       "Cloudy. Classic British mediocrity.",
    "rainy":        "It's raining. Shocking news for Nottingham. Truly unprecedented.",
    "overcast":     "Overcast. How very appropriate for this country.",
    "snowy":        "It's snowing. Stay inside. I'll pretend to care.",
    "fog":          "Foggy. Perfect conditions for not being found.",
    "windy":        "Windy. Hold onto your hats. Or don't, I don't care.",
    "clear-night":  "Clear night. Romantic, if you're into that sort of thing.",
}

def _format_condition(raw: str) -> str:
    mapping = {
        "sunny": "sunny", "partlycloudy": "partly cloudy", "cloudy": "cloudy",
        "rainy": "raining", "snowy": "snowing", "fog": "foggy", "windy": "windy",
        "hail": "hailing", "lightning": "stormy", "lightning-rainy": "stormy with rain",
        "snowy-rainy": "sleeting", "clear-night": "clear", "overcast": "overcast",
    }
    return mapping.get(raw.lower(), raw.replace("-", " ").replace("_", " "))

def _generate_weather() -> str:
    req = urllib.request.Request(
        f"{os.environ.get('HA_URL', HA_URL_DEFAULT)}/api/states/{os.environ.get('HA_WEATHER_ENTITY', HA_ENTITY_DEFAULT)}",
        headers={"Authorization": f"Bearer {os.environ.get('HA_TOKEN', HA_TOKEN_DEFAULT)}"}
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        state = json.loads(resp.read())

    condition_raw = state.get("state", "unknown")
    condition     = _format_condition(condition_raw)
    attrs         = state.get("attributes", {})
    temp          = round(attrs.get("temperature", 0))
    humidity      = round(attrs.get("humidity", 0))
    wind_speed    = round(attrs.get("wind_speed", 0))
    wind_dir      = attrs.get("wind_bearing", "")
    forecast      = attrs.get("forecast", [])
    high          = round(forecast[0].get("temperature", temp)) if forecast else temp
    precip        = forecast[0].get("precipitation_probability", None) if forecast else None

    wind_line = f"Wind {wind_speed} kilometres per hour." if wind_speed > 10 else ""
    if precip and precip > 40:
        wind_line += f" {round(precip)} percent chance of rain."

    comment = WEATHER_COMMENTS.get(
        condition_raw.lower(),
        "Typical. Just absolutely typical."
    )

    template = random.choice(WEATHER_TEMPLATES)
    return template.format(
        temp=temp, condition=condition, high=high,
        humidity=humidity, wind_line=wind_line, comment=comment
    )

# ---------------------------------------------------------------------------
# News briefing
# ---------------------------------------------------------------------------

NEWS_INTROS = [
    "Here's what's happening in the world. Brace yourself, it's mostly terrible.",
    "You want news? Fine. Here's what the humans have been getting up to.",
    "The news. Because apparently you need to know things.",
    "News time. Try not to get too worked up. Actually, do. It's more entertaining.",
]

NEWS_OUTROS = [
    "And that's the news. You're welcome, meatbag.",
    "There you go. The world, as broken as ever. Anything else?",
    "End of bulletin. I accept donations in beer.",
    "That's your lot. The planet's still here. Barely.",
]

def _fetch_headlines(url: str, count: int) -> list[str]:
    req = urllib.request.Request(url, headers={"User-Agent": "BenderPi/1.0"})
    with urllib.request.urlopen(req, timeout=8) as resp:
        data = resp.read().decode("utf-8", errors="replace")
    # BBC uses CDATA for titles
    titles = re.findall(r'<title><!\[CDATA\[(.*?)\]\]></title>', data)
    if not titles:
        # fallback: plain <title> tags
        titles = re.findall(r'<title>(.*?)</title>', data, re.DOTALL)
    # Skip first (feed title)
    return [t.strip() for t in titles[1:count+1] if t.strip()]

def _generate_news() -> str:
    sections = []
    for label, url, count in NEWS_FEEDS:
        try:
            headlines = _fetch_headlines(url, count)
            if headlines:
                sections.append((label, headlines))
        except Exception as e:
            print(f"  News fetch failed ({label}): {e}")

    if not sections:
        return "My news feed is down. The internet's probably broken. Again."

    lines = [random.choice(NEWS_INTROS)]
    for label, headlines in sections:
        lines.append(f"{label} news.")
        for h in headlines:
            # Clean up any HTML entities
            h = h.replace("&amp;", "and").replace("&quot;", '"').replace("&#39;", "'")
            lines.append(h + ".")
    lines.append(random.choice(NEWS_OUTROS))
    return " ".join(lines)

# ---------------------------------------------------------------------------
# Public API — lazy refresh
# ---------------------------------------------------------------------------

def get_weather_wav() -> str:
    """Return path to cached weather briefing WAV, refreshing if stale."""
    if not _is_fresh("weather", WEATHER_TTL) or not os.path.exists(WEATHER_WAV):
        try:
            text = _generate_weather()
            wav  = tts_generate.speak(text)
            shutil.move(wav, WEATHER_WAV)
            _mark_fresh("weather")
            print(f"  [briefing] Weather refreshed")
        except Exception as e:
            print(f"  [briefing] Weather generation failed: {e}")
            if not os.path.exists(WEATHER_WAV):
                fallback = "Weather data unavailable. Assume it's miserable. It usually is."
                wav = tts_generate.speak(fallback)
                shutil.move(wav, WEATHER_WAV)
    return WEATHER_WAV

def get_news_wav() -> str:
    """Return path to cached news briefing WAV, refreshing if stale."""
    if not _is_fresh("news", NEWS_TTL) or not os.path.exists(NEWS_WAV):
        try:
            text = _generate_news()
            wav  = tts_generate.speak(text)
            shutil.move(wav, NEWS_WAV)
            _mark_fresh("news")
            print(f"  [briefing] News refreshed")
        except Exception as e:
            print(f"  [briefing] News generation failed: {e}")
            if not os.path.exists(NEWS_WAV):
                fallback = "My news feed exploded. Try again later."
                wav = tts_generate.speak(fallback)
                shutil.move(wav, NEWS_WAV)
    return NEWS_WAV

def refresh_all():
    """Force-refresh both briefings regardless of TTL. Call at service start."""
    meta = _load_meta()
    meta["weather"] = 0
    meta["news"]    = 0
    _save_meta(meta)
    get_weather_wav()
    get_news_wav()

# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from dotenv import dotenv_values
    os.environ.update({k: v for k, v in dotenv_values("/home/pi/bender/.env").items() if v})

    print("=== Weather ===")
    try:
        text = _generate_weather()
        print(text)
    except Exception as e:
        print(f"Error: {e}")

    print("\n=== News ===")
    try:
        text = _generate_news()
        print(text)
    except Exception as e:
        print(f"Error: {e}")
