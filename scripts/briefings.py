#!/usr/bin/env python3
"""
briefings.py — Pre-generated TTS briefings for weather, news, and time.

Briefings are cached as WAV files and refreshed when stale.
Default TTLs (overridable via bender_config.json):
  weather: 30 minutes
  news:    2 hours
  time:    60 seconds

Usage:
    from briefings import get_weather_wav, get_news_wav, get_time_wav
    wav = get_weather_wav()                        # home weather
    wav = get_weather_wav_for_location("Tokyo")    # any location (cached)
    wav = get_time_wav()                           # local time
    wav = get_time_wav("America/New_York")         # remote timezone
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
from dataclasses import dataclass
from typing import Callable

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tts_generate
from config import cfg as _cfg
from logger import get_logger
from metrics import metrics

log = get_logger("briefings")

BASE_DIR    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAILY_DIR   = os.path.join(BASE_DIR, "speech", "responses", "daily")
WEATHER_WAV = os.path.join(DAILY_DIR, "weather_briefing.wav")
NEWS_WAV    = os.path.join(DAILY_DIR, "news_briefing.wav")
META_PATH   = os.path.join(DAILY_DIR, "briefings_meta.json")

WEATHER_TTL = int(_cfg.briefings_weather_ttl_s)
NEWS_TTL    = int(_cfg.briefings_news_ttl_s)
TIME_TTL    = 60

NEWS_FEEDS  = [tuple(f) for f in _cfg.briefings_news_feeds]

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


def _save_meta(meta: dict) -> None:
    with _meta_lock:
        with open(META_PATH, "w") as f:
            json.dump(meta, f)


def _is_fresh(key: str, ttl: int) -> bool:
    meta = _load_meta()
    return (time.time() - meta.get(key, 0)) < ttl


def _mark_fresh(key: str) -> None:
    meta = _load_meta()
    meta[key] = time.time()
    _save_meta(meta)


def _invalidate(key: str) -> None:
    meta = _load_meta()
    meta[key] = 0
    _save_meta(meta)


# ---------------------------------------------------------------------------
# Core cache + TTS pattern
# ---------------------------------------------------------------------------

@dataclass
class BriefingSource:
    key: str
    ttl: int
    wav_path: str
    generate_text: Callable[[], str]
    fallback_text: str


def _get_briefing_wav(
    key: str,
    ttl: int,
    wav_path: str,
    generate_text: Callable[[], str],
    fallback_text: str,
) -> str:
    """Check TTL + existence, regenerate if stale, return wav_path."""
    if not _is_fresh(key, ttl) or not os.path.exists(wav_path):
        try:
            text = generate_text()
            with metrics.timer("briefing_generate", briefing=key):
                wav = tts_generate.speak(text)
            shutil.move(wav, wav_path)
            _mark_fresh(key)
            log.info("[briefing] %s refreshed", key)
        except Exception as e:
            log.error("[briefing] %s generation failed: %s", key, e)
            metrics.count("briefing_generation_failed", briefing=key)
            if not os.path.exists(wav_path):
                wav = tts_generate.speak(fallback_text)
                shutil.move(wav, wav_path)
    return wav_path


# ---------------------------------------------------------------------------
# Weather briefing — home location (HA)
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
        with urllib.request.urlopen(req, timeout=float(_cfg.http_timeout_s)) as resp:
            result = json.loads(resp.read())
        return result.get("service_response", {}).get(entity, {}).get("forecast", [])
    except Exception as e:
        log.warning("[briefing] Forecast service call failed: %s", e)
        return []


def get_weather_text() -> str:
    ha_url = _cfg.ha_url
    token  = _cfg.ha_token
    entity = _cfg.ha_weather_entity

    req = urllib.request.Request(
        f"{ha_url}/api/states/{entity}",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=float(_cfg.http_timeout_s)) as resp:
        state = json.loads(resp.read())

    condition_raw = state.get("state", "unknown")
    condition     = _format_condition(condition_raw)
    attrs         = state.get("attributes", {})
    temp          = round(attrs.get("temperature", 0))
    humidity      = round(attrs.get("humidity", 0))
    wind_speed    = round(attrs.get("wind_speed", 0))

    forecast  = _get_forecast(ha_url, token, entity)
    high      = round(forecast[0].get("temperature", temp)) if forecast else temp
    precip    = forecast[0].get("precipitation_probability", None) if forecast else None

    wind_line = f"Wind {wind_speed} kilometres per hour." if wind_speed > 10 else ""
    if precip and precip > 40:
        wind_line += f" {round(precip)} percent chance of rain."

    comment = WEATHER_COMMENTS.get(condition_raw.lower(), "Typical. Just absolutely typical.")
    return random.choice(WEATHER_TEMPLATES).format(
        temp=temp, condition=condition, high=high,
        humidity=humidity, wind_line=wind_line, comment=comment,
    )


def get_weather_wav() -> str:
    """Return path to cached home weather WAV, refreshing if stale."""
    return _get_briefing_wav(
        "weather", WEATHER_TTL, WEATHER_WAV, get_weather_text,
        "Weather data unavailable. Assume it's miserable. It usually is.",
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
    with urllib.request.urlopen(req, timeout=float(_cfg.http_timeout_s)) as resp:
        data = resp.read().decode("utf-8", errors="replace")
    titles = re.findall(r'<title><!\[CDATA\[(.*?)\]\]></title>', data)
    if not titles:
        titles = re.findall(r'<title>(.*?)</title>', data, re.DOTALL)
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
            h = h.replace("&amp;", "and").replace("&quot;", '"').replace("&#39;", "'")
            lines.append(h + ".")
    lines.append(random.choice(NEWS_OUTROS))
    return " ".join(lines)


def get_news_wav() -> str:
    """Return path to cached news WAV, refreshing if stale."""
    return _get_briefing_wav(
        "news", NEWS_TTL, NEWS_WAV, get_news_text,
        "My news feed exploded. Try again later.",
    )


# ---------------------------------------------------------------------------
# Time briefing — any timezone
# ---------------------------------------------------------------------------

def get_time_text(timezone: str | None = None) -> str:
    from zoneinfo import ZoneInfo
    from datetime import datetime
    tz_name = timezone or getattr(_cfg, "timezone", "Europe/London")
    now = datetime.now(ZoneInfo(tz_name))
    hour = now.strftime("%I").lstrip("0") or "12"
    minute = now.strftime("%M")
    ampm = now.strftime("%p").lower()
    time_str = f"{hour} {ampm}" if minute == "00" else f"{hour} {minute} {ampm}"
    if timezone:
        return f"It's {time_str} in {timezone.replace('_', ' ')}."
    return f"It's {time_str}."


def get_time_wav(timezone: str | None = None) -> str:
    """Return cached WAV for current time. 60s TTL — always nearly fresh."""
    key = "time_" + re.sub(r"[^a-z0-9]", "_", (timezone or "local").lower())
    wav_path = os.path.join(DAILY_DIR, f"{key}.wav")
    return _get_briefing_wav(
        key, TIME_TTL, wav_path,
        lambda: get_time_text(timezone),
        "I have no idea what time it is. Ask a clock.",
    )


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

_FEATURE_RANK = {"PCLI": 4, "PCLP": 4, "PCLS": 4, "ADM1": 3, "ADM2": 2, "PPLA": 1, "PPL": 0}


def _geocode(location: str) -> tuple[float, float, str, str] | None:
    import urllib.parse
    params = urllib.parse.urlencode({"name": location, "count": 10, "language": "en", "format": "json"})
    req = urllib.request.Request(f"{_OPEN_METEO_GEOCODE}?{params}")
    try:
        with urllib.request.urlopen(req, timeout=float(_cfg.http_timeout_s)) as r:
            data = json.loads(r.read())
        results = data.get("results", [])
        if not results:
            return None
        best = max(results, key=lambda r: (_FEATURE_RANK.get(r.get("feature_code", ""), 0), r.get("population") or 0))
        return best["latitude"], best["longitude"], best["name"], best.get("country", "")
    except Exception as e:
        log.warning("[briefing] Geocode failed for %r: %s", location, e)
        return None


def get_weather_text_for_location(location: str) -> str:
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
    with urllib.request.urlopen(req, timeout=float(_cfg.http_timeout_s)) as r:
        w = json.loads(r.read())

    cur   = w["current"]
    daily = w["daily"]

    wmo        = int(cur["weathercode"])
    temp       = round(cur["temperature_2m"])
    wind_speed = round(cur.get("windspeed_10m", 0))
    condition  = _WMO_CONDITION.get(wmo, "doing something unusual")
    high       = round(daily["temperature_2m_max"][0])
    precip_pct = daily.get("precipitation_probability_max", [None])[0]

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
    """Return cached WAV for location-specific weather. Caller must NOT delete."""
    key = "weather_" + re.sub(r"[^a-z0-9]", "_", location.lower())
    wav_path = os.path.join(DAILY_DIR, f"{key}.wav")
    return _get_briefing_wav(
        key, WEATHER_TTL, wav_path,
        lambda: get_weather_text_for_location(location),
        f"Weather for {location} is unavailable. Assume it's miserable.",
    )


# ---------------------------------------------------------------------------
# Registered sources — used by refresh_all()
# ---------------------------------------------------------------------------

_SOURCES: list[BriefingSource] = [
    BriefingSource(
        key="weather", ttl=WEATHER_TTL, wav_path=WEATHER_WAV,
        generate_text=get_weather_text,
        fallback_text="Weather data unavailable. Assume it's miserable. It usually is.",
    ),
    BriefingSource(
        key="news", ttl=NEWS_TTL, wav_path=NEWS_WAV,
        generate_text=get_news_text,
        fallback_text="My news feed exploded. Try again later.",
    ),
]


def refresh_all() -> None:
    """Force-refresh all registered briefings. Call at service start."""
    for source in _SOURCES:
        _invalidate(source.key)
    for source in _SOURCES:
        _get_briefing_wav(
            source.key, source.ttl, source.wav_path,
            source.generate_text, source.fallback_text,
        )


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from dotenv import dotenv_values
    os.environ.update({k: v for k, v in dotenv_values("/home/pi/bender/.env").items() if v})

    print("=== Weather (home) ===")
    try:
        print(get_weather_text())
    except Exception as e:
        print(f"Error: {e}")

    print("\n=== News ===")
    try:
        print(get_news_text())
    except Exception as e:
        print(f"Error: {e}")

    print("\n=== Time (local) ===")
    print(get_time_text())

    print("\n=== Weather (Tokyo) ===")
    try:
        print(get_weather_text_for_location("Tokyo"))
    except Exception as e:
        print(f"Error: {e}")
