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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tts_generate

HA_URL   = os.environ.get("HA_URL",   "http://192.168.68.125:8123")
HA_TOKEN = os.environ.get("HA_TOKEN", "")

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
    "motion_sound", "bedroom_do_not", "lincolns_room_do_not", "martins_office_do",
    "living_room_do_not",
}

# Entity IDs to always exclude even if they pass the keyword filter
EXCLUDE_ENTITIES = {
    "switch.clock_weather_card_pre_release",
    "switch.entrance_keypad_chirps",
    "switch.keypad_07069_chirps",
    "switch.403100527506007695_bsh_common_setting_powerstate",
    "switch.ensuite_radiator_plug_hive",
    "switch.assist_microphone_mute",
    "switch.lightswitches",
    # Conservatory sub-components (prefer switch.conservatory_lights + conservatory_lamp)
    "switch.conservatory_lights_led",
    "switch.conservatory_lamp_led",
    "switch.led_tree_lights_conservatory",
    # Closet is not a room people ask for by name (part of bedroom physically)
    "switch.shelly_bedroom_wall_switch_switch_1",
    # Duplicate/hex-named climate entities (prefer friendly-named ones)
    "climate.0xc09b9efffe848bb4",
    "climate.martins_office_radiator_trv_2",
}

# ---------------------------------------------------------------------------
# Entity cache (refreshed every 60 seconds)
# ---------------------------------------------------------------------------

_cache: list[dict] = []
_cache_ts: float   = 0.0
CACHE_TTL = 60.0   # seconds


def _fetch_entities() -> list[dict]:
    """Fetch and filter controllable entities from HA."""
    req = urllib.request.Request(
        f"{HA_URL}/api/states",
        headers={"Authorization": f"Bearer {HA_TOKEN}"},
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
            print(f"HA entity fetch failed: {e}")
    return _cache


# ---------------------------------------------------------------------------
# Name normalisation + matching
# ---------------------------------------------------------------------------

def _normalise(text: str) -> str:
    """Lowercase, split CamelCase, strip common suffixes, clean up."""
    # Split CamelCase: "MartinsOffice" → "martins office"
    text = re.sub(r'([a-z])([A-Z])', r'\1 \2', text)
    text = text.lower()
    # Remove hyphens/underscores
    text = re.sub(r'[-_]', ' ', text)
    # Strip lighting noise words when matching (keep in display)
    for word in (' light', ' lights', ' lamp', ' led', ' wall', ' led wall'):
        text = text.replace(word, '')
    return text.strip()


def _find_entities(user_term: str) -> list[dict]:
    """
    Match user_term against all controllable entities.
    Returns list of matching entity dicts (may be multiple for a room).
    """
    entities = _get_entities()
    term     = _normalise(user_term)

    matches = []
    for e in entities:
        name_norm = _normalise(e["friendly_name"])
        id_norm   = _normalise(e["entity_id"].replace(".", " "))
        if term in name_norm or term in id_norm:
            matches.append(e)

    return matches


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

    # Remove trailing noise words
    for word in ("light", "lights", "lamp", "switch", "on", "off"):
        t = re.sub(rf"\b{word}\b", "", t)

    return t.strip() or None


# ---------------------------------------------------------------------------
# HA service call
# ---------------------------------------------------------------------------

def _ha_call(domain: str, service: str, entity_id: str,
             extra: dict | None = None) -> bool:
    url  = f"{HA_URL}/api/services/{domain}/{service}"
    body = {"entity_id": entity_id}
    if extra:
        body.update(extra)
    data = json.dumps(body).encode()
    req  = urllib.request.Request(
        url, data=data,
        headers={
            "Authorization": f"Bearer {HA_TOKEN}",
            "Content-Type":  "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status in (200, 201)
    except Exception as e:
        print(f"  HA call failed ({entity_id}): {e}")
        return False


def _parse_temperature(text: str) -> float | None:
    """Extract a numeric temperature from user text."""
    m = re.search(r"(\d{1,2})(?:\.\d+)?\s*(?:degrees?|deg|°)?", text.lower())
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
    "Something went wrong. HA isn't responding. Probably not my fault.",
    "The smart home isn't feeling very smart right now.",
    "HA call failed. I blame the humans who built this infrastructure.",
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def control(user_text: str) -> str:
    """Parse user_text, call HA, return path to a Bender TTS response WAV."""
    action    = _parse_action(user_text)
    room_term = _parse_room_term(user_text)

    if not room_term:
        return tts_generate.speak(random.choice(UNKNOWN_ROOM_RESPONSES))

    matches = _find_entities(room_term)

    if not matches:
        text = f"No idea what {room_term!r} is. Check I have access to it in HA, or try a different name."
        return tts_generate.speak(text)

    if action is None:
        text = "On or off? Even I need a bit more to go on."
        return tts_generate.speak(text)

    # Check for temperature set request first
    target_temp = _parse_temperature(user_text)
    if target_temp and any(e["domain"] == "climate" for e in matches):
        success = False
        for e in [x for x in matches if x["domain"] == "climate"]:
            print(f"  HA: climate.set_temperature → {e['entity_id']} @ {target_temp}°")
            if _ha_call("climate", "set_temperature", e["entity_id"], {"temperature": target_temp}):
                success = True
        if success:
            room_name = _normalise(matches[0]["friendly_name"]).title()
            text = f"Temperature set to {int(target_temp)} degrees in {room_name}. Don't blame me if you melt."
            return tts_generate.speak(text)
        return tts_generate.speak(random.choice(FAILED_RESPONSES))

    if action is None:
        text = "On or off? Even I need a bit more to go on."
        return tts_generate.speak(text)

    service = f"turn_{action}"
    success = False
    for e in matches:
        domain = e["domain"]
        if domain == "climate":
            # climate uses set_hvac_mode, not turn_on/off
            hvac_mode = "heat" if action == "on" else "off"
            svc = "set_hvac_mode"
            print(f"  HA: climate.set_hvac_mode({hvac_mode}) → {e['entity_id']}")
            if _ha_call(domain, svc, e["entity_id"], {"hvac_mode": hvac_mode}):
                success = True
        else:
            print(f"  HA: {domain}.{service} → {e['entity_id']}")
            if _ha_call(domain, service, e["entity_id"]):
                success = True

    if not success:
        return tts_generate.speak(random.choice(FAILED_RESPONSES))

    names       = list({e["friendly_name"] for e in matches})
    display     = _normalise(names[0]).title() if names else room_term.title()
    templates   = ON_RESPONSES if action == "on" else OFF_RESPONSES
    return tts_generate.speak(random.choice(templates).format(room=display))


# ---------------------------------------------------------------------------
# Standalone test (no audio)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from dotenv import dotenv_values
    env = dotenv_values("/home/pi/bender/.env")
    os.environ.update({k: v for k, v in env.items() if v})

    tests = [
        "turn on the lights in my office",
        "turn off the kitchen lights",
        "lights on in lincolns room",
        "bedroom lights off",
        "conservatory lights on",
    ]
    for t in tests:
        action    = _parse_action(t)
        room_term = _parse_room_term(t)
        matches   = _find_entities(room_term or "")
        names     = [e["friendly_name"] for e in matches]
        print(f"\n  > {t!r}")
        print(f"    action={action!r}, room_term={room_term!r}")
        print(f"    matched: {names}")
