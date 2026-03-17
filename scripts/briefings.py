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
import threading
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tts_generate
from config import cfg as _cfg
from logger import get_logger
from metrics import metrics

log = get_logger("briefings")

BASE_DIR     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAILY_DIR    = os.path.join(BASE_DIR, "speech", "responses", "daily")
WEATHER_WAV  = os.path.join(DAILY_DIR, "weather_briefing.wav")
NEWS_WAV     = os.path.join(DAILY_DIR, "news_briefing.wav")
META_PATH    = os.path.join(DAILY_DIR, "briefings_meta.json")

WEATHER_TTL  = 30 * 60    # 30 minutes
NEWS_TTL     = 2 * 60 * 60  # 2 hours

HA_URL_DEFAULT    = "http://homeassistant.local:8123"
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

_meta_lock = threading.Lock()

def _load_meta() -> dict:
    with _meta_lock:
        try:
            with open(META_PATH) as f:
                return json.load(f)
        except Exception:
            return {}

def _save_meta(meta: dict):
    with _meta_lock:
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
    "rainy":        f"It's raining. Shocking news for {_cfg.location}. Truly unprecedented.",
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

def _get_forecast(ha_url: str, token: str, entity: str) -> list:
    """Fetch daily forecast via weather.get_forecasts service (HA 2023.9+)."""
    import urllib.parse
    data = json.dumps({"entity_id": entity, "type": "daily"}).encode()
    req = urllib.request.Request(
        f"{ha_url}/api/services/weather/get_forecasts?return_response",
        data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            result = json.loads(resp.read())
        return result.get("service_response", {}).get(entity, {}).get("forecast", [])
    except Exception as e:
        log.warning("[briefing] Forecast service call failed: %s", e)
        return []

def get_weather_text() -> str:
    ha_url  = os.environ.get("HA_URL", HA_URL_DEFAULT)
    token   = os.environ.get("HA_TOKEN", HA_TOKEN_DEFAULT)
    entity  = os.environ.get("HA_WEATHER_ENTITY", HA_ENTITY_DEFAULT)

    req = urllib.request.Request(
        f"{ha_url}/api/states/{entity}",
        headers={"Authorization": f"Bearer {token}"}
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        state = json.loads(resp.read())

    condition_raw = state.get("state", "unknown")
    condition     = _format_condition(condition_raw)
    attrs         = state.get("attributes", {})
    temp          = round(attrs.get("temperature", 0))
    humidity      = round(attrs.get("humidity", 0))
    wind_speed    = round(attrs.get("wind_speed", 0))

    # Fetch daily forecast via service call (attrs["forecast"] is empty in modern HA)
    forecast      = _get_forecast(ha_url, token, entity)
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

def get_news_text() -> str:
    sections = []
    for label, url, count in NEWS_FEEDS:
        try:
            headlines = _fetch_headlines(url, count)
            if headlines:
                sections.append((label, headlines))
        except Exception as e:
            log.warning("News fetch failed (%s): %s", label, e)

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
            text = get_weather_text()
            with metrics.timer("briefing_generate", briefing="weather"):
                wav = tts_generate.speak(text)
            shutil.move(wav, WEATHER_WAV)
            _mark_fresh("weather")
            log.info("[briefing] Weather refreshed")
        except Exception as e:
            log.error("[briefing] Weather generation failed: %s", e)
            if not os.path.exists(WEATHER_WAV):
                fallback = "Weather data unavailable. Assume it's miserable. It usually is."
                wav = tts_generate.speak(fallback)
                shutil.move(wav, WEATHER_WAV)
    return WEATHER_WAV

def get_news_wav() -> str:
    """Return path to cached news briefing WAV, refreshing if stale."""
    if not _is_fresh("news", NEWS_TTL) or not os.path.exists(NEWS_WAV):
        try:
            text = get_news_text()
            with metrics.timer("briefing_generate", briefing="news"):
                wav = tts_generate.speak(text)
            shutil.move(wav, NEWS_WAV)
            _mark_fresh("news")
            log.info("[briefing] News refreshed")
        except Exception as e:
            log.error("[briefing] News generation failed: %s", e)
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
# Location-specific weather (Open-Meteo, no API key required)
# ---------------------------------------------------------------------------

_OPEN_METEO_GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
_OPEN_METEO_WEATHER = "https://api.open-meteo.com/v1/forecast"

_WMO_CONDITION = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "foggy", 48: "foggy",
    51: "drizzling", 53: "drizzling", 55: "heavy drizzle",
    61: "raining", 63: "raining", 65: "heavy rain",
    71: "snowing", 73: "snowing", 75: "heavy snow",
    80: "showery", 81: "showery", 82: "heavy showers",
    95: "stormy", 96: "stormy", 99: "stormy",
}

_WMO_COMMENT = {
    0: "Clear sky. Disgusting. I preferred it when everything was grey.",
    1: "Mainly clear. Suspicious.",
    2: "Partly cloudy. Make your mind up, sky.",
    3: "Overcast. Perfectly miserable.",
    45: "Foggy. Perfect conditions for not being found.",
    48: "Foggy. Perfect conditions for not being found.",
    61: "Raining. Shocking. Unprecedented.",
    63: "Raining. Shocking. Unprecedented.",
    65: "Heavy rain. Someone up there really hates you.",
    71: "Snowing. Stay inside. I'll pretend to care.",
    73: "Snowing. Stay inside. I'll pretend to care.",
    75: "Heavy snow. Absolutely not going outside.",
    80: "Showery. Typical.",
    81: "Showery. Typical.",
    95: "Thunderstorms. Dramatic. I approve.",
}


# Feature codes ranked by preference: country > state/region > city > town
_FEATURE_RANK = {"PCLI": 4, "PCLP": 4, "PCLS": 4, "ADM1": 3, "ADM2": 2, "PPLA": 1, "PPL": 0}

def _geocode(location: str) -> tuple[float, float, str, str] | None:
    """Return (lat, lon, resolved_name, country) or None if not found.

    Fetches multiple candidates and prefers countries/regions over small towns.
    """
    import urllib.parse
    params = urllib.parse.urlencode({"name": location, "count": 10, "language": "en", "format": "json"})
    req = urllib.request.Request(f"{_OPEN_METEO_GEOCODE}?{params}")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
        results = data.get("results", [])
        if not results:
            return None
        # Score each result: prefer higher feature rank, then higher population
        def _score(r):
            rank = _FEATURE_RANK.get(r.get("feature_code", ""), 0)
            pop  = r.get("population") or 0
            return (rank, pop)
        best = max(results, key=_score)
        return best["latitude"], best["longitude"], best["name"], best.get("country", "")
    except Exception as e:
        log.warning("[briefing] Geocode failed for %r: %s", location, e)
        return None


def get_weather_text_for_location(location: str) -> str:
    """Fetch current weather + daily forecast for any location via Open-Meteo."""
    geo = _geocode(location)
    if not geo:
        return f"I have no idea where {location} is. Sounds made up."
    lat, lon, place_name, country = geo

    import urllib.parse
    params = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon,
        "current": "temperature_2m,precipitation,weathercode,windspeed_10m,relative_humidity_2m",
        "daily": "temperature_2m_max,precipitation_probability_max",
        "timezone": "auto",
        "forecast_days": 1,
    })
    req = urllib.request.Request(f"{_OPEN_METEO_WEATHER}?{params}")
    with urllib.request.urlopen(req, timeout=5) as r:
        w = json.loads(r.read())

    cur = w["current"]
    daily = w["daily"]

    wmo         = int(cur["weathercode"])
    temp        = round(cur["temperature_2m"])
    wind_speed  = round(cur.get("windspeed_10m", 0))
    condition   = _WMO_CONDITION.get(wmo, "doing something unusual")
    high        = round(daily["temperature_2m_max"][0])
    precip_pct  = daily.get("precipitation_probability_max", [None])[0]

    location_label = place_name if not country else f"{place_name}, {country}"
    wind_line = f"Wind at {wind_speed} kilometres per hour." if wind_speed > 10 else ""
    if precip_pct and precip_pct > 40:
        wind_line += f" {round(precip_pct)} percent chance of rain."

    comment = _WMO_COMMENT.get(wmo, "Typical. Just absolutely typical.")

    templates = [
        f"In {location_label}: {temp} degrees and {condition}. High of {high} today. {wind_line} {comment}",
        f"Weather in {location_label}: {temp} degrees, {condition}. Today's high: {high}. {wind_line} {comment}",
    ]
    return random.choice(templates).strip()


def get_weather_wav_for_location(location: str) -> str:
    """Generate and return a temp WAV for location-specific weather. Caller must delete."""
    text = get_weather_text_for_location(location)
    return tts_generate.speak(text)


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from dotenv import dotenv_values
    os.environ.update({k: v for k, v in dotenv_values("/home/pi/bender/.env").items() if v})

    print("=== Weather ===")
    try:
        text = get_weather_text()
        print(text)
    except Exception as e:
        print(f"Error: {e}")

    print("\n=== News ===")
    try:
        text = get_news_text()
        print(text)
    except Exception as e:
        print(f"Error: {e}")
