#!/usr/bin/env python3
"""
ha_control.py — Dynamic HA device control via REST API.

Fetches the live entity list from HA at call time (cached 60s) so new
devices are picked up automatically without any config changes.

Filters to controllable lights and switches, normalises friendly names,
and matches the user's spoken room/device term against them.
"""

import os
import re
import sys
import json
import random
import time
import urllib.request
from difflib import SequenceMatcher

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tts_generate
from config import cfg
from logger import get_logger
from metrics import metrics

log = get_logger("ha_control")

# ---------------------------------------------------------------------------
# Entity noise filter — sub-components and non-lighting switches to exclude
# ---------------------------------------------------------------------------

EXCLUDE_KEYWORDS = {
    "nightlight", "sync_send", "sync_receive", "reverse", "crossfade",
    "loudness", "surround", "subwoofer", "speech_enhancement", "night_sound",
    "snooze", "ding_sound", "motion_detection", "live_stream", "event_stream",
    "siren", "permit_join", "report_state", "do_not_disturb", "screensaver",
    "maintenance_mode", "kiosk_lock", "auto_off_enabled", "auto_update_enabled",
    "child_lock", "open_window", "smart_temperature_control", "flip_indicator",
    "power_outage_memory", "led_disabled_night", "schedule_", "powerwall",
    "dishwasher", "storm_watch", "cloud_", "fire_tablet", "turbo_mode",
    "network_indicator", "delayed_power_on", "detach_relay", "weather_card",
    "keypad_chirps", "radiator_plug", "assist_microphone", "dishcare",
    "extractor_fan", "fly_zapper", "heated_airer", "lightswitches",
    "alarm_siren", "motion_warning", "upstairs_snooze", "downstairs_snooze",
    "motion_sound", "do_not",  # catches all do_not_disturb sub-entities
}

# Entity IDs to always exclude — configured in bender_config.json under
# "ha_exclude_entities". Add any sub-component or duplicate entities specific
# to your HA setup that should never be controlled by voice.
def _load_exclude_entities() -> set:
    return set(cfg.ha_exclude_entities)

EXCLUDE_ENTITIES = _load_exclude_entities()

# ---------------------------------------------------------------------------
# Entity cache (refreshed every 60 seconds)
# ---------------------------------------------------------------------------

_cache: list[dict] = []
_cache_ts: float   = 0.0
CACHE_TTL = 60.0   # seconds


def _fetch_entities() -> list[dict]:
    """Fetch and filter controllable entities from HA."""
    req = urllib.request.Request(
        f"{cfg.ha_url}/api/states",
        headers={"Authorization": f"Bearer {cfg.ha_token}"},
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        all_states = json.loads(resp.read())

    result = []
    for s in all_states:
        eid   = s["entity_id"]
        state = s.get("state", "unavailable")

        # Only lights and switches
        if not eid.startswith(("light.", "switch.", "climate.")):
            continue
        # Skip unavailable/unknown
        if state in ("unavailable", "unknown"):
            continue
        # Skip excluded entities
        if eid in EXCLUDE_ENTITIES:
            continue
        # Skip if any noise keyword in entity_id
        if any(kw in eid for kw in EXCLUDE_KEYWORDS):
            continue

        result.append({
            "entity_id":    eid,
            "domain":       eid.split(".")[0],
            "state":        state,
            "friendly_name": s.get("attributes", {}).get("friendly_name", eid),
        })
    return result


def _get_entities() -> list[dict]:
    global _cache, _cache_ts
    if time.time() - _cache_ts > CACHE_TTL:
        try:
            _cache    = _fetch_entities()
            _cache_ts = time.time()
        except Exception as e:
            log.error("HA entity fetch failed: %s", e)
    return _cache


# ---------------------------------------------------------------------------
# Name normalisation + matching
# ---------------------------------------------------------------------------

def _normalise(text: str) -> str:
    """Lowercase, split CamelCase, strip common suffixes, clean up."""
    # Split CamelCase: "MartinsOffice" → "martins office"
    text = re.sub(r'([a-z])([A-Z])', r'\1 \2', text)
    text = text.lower()
    # Strip apostrophes so "martin's" matches "martins"
    text = re.sub(r"'", '', text)
    # Remove hyphens/underscores
    text = re.sub(r'[-_]', ' ', text)
    # Strip lighting noise words when matching (keep in display)
    for word in (' light', ' lights', ' lamp', ' led', ' wall', ' led wall'):
        text = text.replace(word, '')
    return text.strip()


def _token_score(user_tokens: set, entity_tokens: set) -> float:
    """Fraction of user tokens found in entity name. 1.0 = all matched."""
    if not user_tokens:
        return 0.0
    return len(user_tokens & entity_tokens) / len(user_tokens)


def _fuzzy_token_score(user_tokens: set, entity_tokens: set) -> float:
    """Best fuzzy match for each user token against entity tokens."""
    if not user_tokens or not entity_tokens:
        return 0.0
    total = 0.0
    for ut in user_tokens:
        best = max(
            SequenceMatcher(None, ut, et).ratio()
            for et in entity_tokens
        )
        total += best
    return total / len(user_tokens)


def _find_entities(room_term: str, entities: list = None) -> list:
    """Find entities matching room_term using token scoring with fuzzy fallback."""
    if entities is None:
        entities = _get_entities()

    term = room_term.lower().strip()
    # Synonym expansion
    term = cfg.ha_room_synonyms.get(term, term)

    term_norm = _normalise(term)
    user_tokens = set(term_norm.split())

    scored = []
    for entity in entities:
        name = entity.get("attributes", {}).get("friendly_name",
               entity.get("friendly_name", ""))
        eid = entity.get("entity_id", "")
        name_norm = _normalise(name)
        id_norm = _normalise(eid.replace(".", " "))
        entity_tokens = set(name_norm.split()) | set(id_norm.split())
        score = _token_score(user_tokens, entity_tokens)
        scored.append((entity, score))

    best_exact = max((s for _, s in scored), default=0.0)
    if best_exact < 0.5:
        # Phase 2: fuzzy fallback
        scored = []
        for entity in entities:
            name = entity.get("attributes", {}).get("friendly_name",
                   entity.get("friendly_name", ""))
            eid = entity.get("entity_id", "")
            name_norm = _normalise(name)
            id_norm = _normalise(eid.replace(".", " "))
            entity_tokens = set(name_norm.split()) | set(id_norm.split())
            score = _fuzzy_token_score(user_tokens, entity_tokens)
            scored.append((entity, score))

    threshold = 0.5
    matches = [(e, s) for e, s in scored if s >= threshold]
    matches.sort(key=lambda x: x[1], reverse=True)

    if not matches:
        closest = sorted(scored, key=lambda x: x[1], reverse=True)[:3]
        log.debug("No match for %r. Closest: %s",
                  room_term,
                  [(e.get("attributes", {}).get("friendly_name",
                    e.get("friendly_name", "?")), round(s, 2))
                   for e, s in closest])
        return []

    return [e for e, s in matches]


# ---------------------------------------------------------------------------
# Action parsing
# ---------------------------------------------------------------------------

def _parse_action(text: str) -> str | None:
    t = text.lower()
    if re.search(r"\b(off|turn off|switch off|disable|kill|cut)\b", t):
        return "off"
    if re.search(r"\b(on|turn on|switch on|enable|put on)\b", t):
        return "on"
    return None


def _parse_room_term(text: str) -> str | None:
    """
    Extract a room/device term from user text by removing action words.
    E.g. "turn on the lights in my office" → "office"
         "kitchen lights off"              → "kitchen"
    """
    t = text.lower()
    # Strip action phrases
    for phrase in (
        "turn on the lights in", "turn off the lights in",
        "turn on the", "turn off the",
        "switch on the", "switch off the",
        "lights on in", "lights off in",
        "lights on", "lights off",
        "light on", "light off",
        "turn on", "turn off",
        "switch on", "switch off",
        "can you", "please", "bender",
        "the lights in", "lights in", "light in",
        "my ", "the ", "a ",
    ):
        t = t.replace(phrase, " ")

    # Strip reversed-order action verbs: "turn the heating off" → strip "turn"
    t = re.sub(r"\b(turn|switch|put)\b", " ", t)
    # Strip bare prepositions left after phrase removal
    t = re.sub(r"\b(in|at|for|of|the|a)\b", " ", t)

    # Strip temperature-set phrasing: "set X to 20 degrees"
    t = re.sub(r"\bset\b", " ", t)
    t = re.sub(r"\bto\s+\d+(?:\.\d+)?\s*(?:degrees?|deg)?\b", " ", t)
    t = re.sub(r"\d+(?:\.\d+)?\s*(?:degrees?|deg)\b", " ", t)

    # Remove trailing noise words (lights and heating device terms)
    for word in ("light", "lights", "lamp", "switch", "on", "off",
                 "radiator", "thermostat", "heating", "temperature", "temp", "trv"):
        t = re.sub(rf"\b{word}\b", "", t)

    return t.strip() or None


# ---------------------------------------------------------------------------
# HA service call
# ---------------------------------------------------------------------------

def _ha_call(domain: str, service: str, entity_id: str,
             extra: dict | None = None) -> bool:
    with metrics.timer("ha_call", entity=entity_id):
        url  = f"{cfg.ha_url}/api/services/{domain}/{service}"
        body = {"entity_id": entity_id}
        if extra:
            body.update(extra)
        data = json.dumps(body).encode()
        req  = urllib.request.Request(
            url, data=data,
            headers={
                "Authorization": f"Bearer {cfg.ha_token}",
                "Content-Type":  "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status in (200, 201)
        except Exception as e:
            log.error("HA call failed (%s): %s", entity_id, e)
            return False


def _parse_temperature(text: str) -> float | None:
    """Extract a numeric temperature from user text."""
    m = re.search(r"(\d{1,2})(?:\.\d+)?\s*(?:degrees?|deg|degrees)?", text.lower())
    return float(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# Response templates
# ---------------------------------------------------------------------------

ON_RESPONSES = [
    "Lights on in the {room}. You're welcome. That'll be five dollars.",
    "Done. {room} is lit up. Like me after a good drink.",
    "Fine. {room} lights are on. Try not to do anything embarrassing.",
    "Switched on. {room}. I'm basically your butler now.",
]
OFF_RESPONSES = [
    "Lights off in the {room}. Darkness suits you.",
    "Done. {room} is dark. Very dramatic. I approve.",
    "Off it goes. {room}. At least now I can't see your face.",
    "Switched off. {room}. Easy. Could do this in my sleep.",
]
UNKNOWN_ROOM_RESPONSES = [
    "Which room? I'm a robot, not a mind reader. Be more specific.",
    "You'll have to be clearer. Office? Kitchen? The chaos you call a bedroom?",
    "I heard you, but I have no idea which room you mean. Try again.",
]
FAILED_RESPONSES = [
    "Something went wrong. Home Assistant isn't responding. Probably not my fault.",
    "The smart home isn't feeling very smart right now.",
    "Home Assistant call failed. I blame the humans who built this infrastructure.",
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def execute(user_text: str) -> dict:
    """Parse user_text, call HA, return structured result.
    Returns: {
        "action": "on" | "off" | "set_temp" | None,
        "entities": [{"entity_id": str, "friendly_name": str, "success": bool}],
        "room_display": str,
        "temperature": float | None,
        "error": None | "no_room" | "no_match" | "no_action" | "ha_failed",
    }
    """
    action = _parse_action(user_text)
    room_term = _parse_room_term(user_text)

    if not room_term:
        return {"action": action, "entities": [], "room_display": "",
                "temperature": None, "error": "no_room"}

    matches = _find_entities(room_term)
    if not matches:
        return {"action": action, "entities": [], "room_display": room_term,
                "temperature": None, "error": "no_match"}

    # For on/off actions, exclude climate entities unless they're the only match
    if action in ("on", "off"):
        non_climate = [e for e in matches if e["domain"] != "climate"]
        if non_climate:
            matches = non_climate

    target_temp = _parse_temperature(user_text)
    if target_temp and any(e["domain"] == "climate" for e in matches):
        results = []
        for e in [x for x in matches if x["domain"] == "climate"]:
            log.info("HA: climate.set_temperature -> %s @ %s deg", e["entity_id"], target_temp)
            success = _ha_call("climate", "set_temperature", e["entity_id"], {"temperature": target_temp})
            results.append({"entity_id": e["entity_id"], "friendly_name": e["friendly_name"], "success": success})
        room_name = _normalise(matches[0]["friendly_name"]).title()
        return {"action": "set_temp", "entities": results, "room_display": room_name,
                "temperature": target_temp, "error": None if any(r["success"] for r in results) else "ha_failed"}

    if action is None:
        return {"action": None, "entities": [], "room_display": room_term,
                "temperature": None, "error": "no_action"}

    results = []
    for e in matches:
        domain = e["domain"]
        if domain == "climate":
            hvac_mode = "heat" if action == "on" else "off"
            log.info("HA: climate.set_hvac_mode(%s) -> %s", hvac_mode, e["entity_id"])
            success = _ha_call(domain, "set_hvac_mode", e["entity_id"], {"hvac_mode": hvac_mode})
        else:
            service = f"turn_{action}"
            log.info("HA: %s.%s -> %s", domain, service, e["entity_id"])
            success = _ha_call(domain, service, e["entity_id"])
        results.append({"entity_id": e["entity_id"], "friendly_name": e["friendly_name"], "success": success})

    names = list({e["friendly_name"] for e in matches})
    display = _normalise(names[0]).title() if names else room_term.title()
    return {"action": action, "entities": results, "room_display": display,
            "temperature": None, "error": None if any(r["success"] for r in results) else "ha_failed"}


def _result_to_speech(result: dict) -> str:
    """Convert execute() result dict to Bender-style speech text."""
    error = result.get("error")
    if error == "no_room":
        return random.choice(UNKNOWN_ROOM_RESPONSES)
    if error == "no_match":
        return f"I can't find anything called {result['room_display']} in Home Assistant. Try saying the room name differently."
    if error == "no_action":
        return "On or off? Even I need a bit more to go on."
    if error == "ha_failed":
        return random.choice(FAILED_RESPONSES)

    action = result["action"]
    room = result["room_display"]
    if action == "set_temp":
        return f"Temperature set to {int(result['temperature'])} degrees in {room}. Don't blame me if you melt."
    templates = ON_RESPONSES if action == "on" else OFF_RESPONSES
    return random.choice(templates).format(room=room)


def control(user_text: str) -> str:
    """Execute + wrap result in Bender TTS. Returns temp WAV path."""
    result = execute(user_text)
    text = _result_to_speech(result)
    return tts_generate.speak(text)


# ---------------------------------------------------------------------------
# Standalone test (no audio)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from dotenv import dotenv_values
    env = dotenv_values("/home/pi/bender/.env")
    os.environ.update({k: v for k, v in env.items() if v})

    tests = [
        "turn on the office lights",
        "turn off the kitchen lights",
        "bedroom lights off",
        "conservatory lights on",
        "set the office radiator to 20 degrees",
    ]
    for t in tests:
        action    = _parse_action(t)
        room_term = _parse_room_term(t)
        matches   = _find_entities(room_term or "")
        names     = [e["friendly_name"] for e in matches]
        print(f"\n  > {t!r}")
        print(f"    action={action!r}, room_term={room_term!r}")
        print(f"    matched: {names}")
