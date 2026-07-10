#!/usr/bin/env python3
"""HA device control orchestrator.

Thin layer: parses intent, resolves entities via EntityRegistry + EntityMatcher,
calls HA via HAClient. Pronoun state (last_entities) lives in the caller.

HA_TOKEN least privilege: this module calls HA's REST API (GET /api/states,
POST /api/services/<domain>/<service>) with whatever token is in .env. Do not
point it at your HA admin user's token -- use a dedicated restricted HA user
scoped to just the light/switch/climate entities BenderPi should touch. See
.env.example for setup steps.
"""

from __future__ import annotations

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tts_generate
from intent import is_ha_question_or_narration
from logger import get_logger
from metrics import metrics

from .entity_matcher import EntityMatcher
from .entity_registry import EntityRegistry
from .ha_client import HAClient, UrllibHAClient

log = get_logger("ha_control")

# Exported so the caller can detect pronouns before calling execute()
PRONOUNS: frozenset[str] = frozenset({"them", "it", "those", "that", "these"})

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
STATUS_RESPONSES = [
    "{summary}. Riveting update, I know.",
    "{summary}. You're welcome for the play-by-play.",
    "{summary}. Happy now?",
]
_ON_STATES = frozenset({"on", "heat", "heating", "auto", "heat_cool"})


def _result_to_speech(result: dict) -> str:
    error = result.get("error")
    if error == "no_room":
        return random.choice(UNKNOWN_ROOM_RESPONSES)
    if error == "no_match":
        return (f"I can't find anything called {result['room_display']} in Home Assistant. "
                "Try saying the room name differently.")
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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def execute(
    user_text: str,
    *,
    registry: EntityRegistry,
    matcher: EntityMatcher,
    client: HAClient,
    last_entities: list[dict] | None = None,
) -> tuple[dict, list[dict]]:
    """Parse user_text, call HA, return (result_dict, matched_entities).

    matched_entities — the entities acted on; caller stores for next-turn
    pronoun resolution.

    result_dict keys:
        action      : "on" | "off" | "set_temp" | None
        entities    : [{"entity_id", "friendly_name", "success"}]
        room_display: str
        temperature : float | None
        error       : None | "no_room" | "no_match" | "no_action" | "ha_failed"
    """
    # Defense in depth: even if intent classification mis-routes a question
    # here (e.g. "is the office light on"), don't let a bare "on"/"off"
    # masquerade as a command — require an explicit "turn on"/"turn off" verb.
    action = matcher.parse_action(user_text, allow_bare=not is_ha_question_or_narration(user_text))
    room_term = matcher.parse_room_term(user_text)

    if not room_term:
        return (
            {"action": action, "entities": [], "room_display": "",
             "temperature": None, "error": "no_room"},
            [],
        )

    # Pronoun resolution — caller supplies last_entities
    if room_term in PRONOUNS and last_entities:
        matches = last_entities
        log.info("Pronoun %r resolved to %s", room_term, [e["entity_id"] for e in matches])
    else:
        matches = matcher.match(room_term, registry.get())

    if not matches:
        return (
            {"action": action, "entities": [], "room_display": room_term,
             "temperature": None, "error": "no_match"},
            [],
        )

    room_display = matches[0]["normalised"].title()

    # For on/off, prefer non-climate entities when mixed results are returned
    if action in ("on", "off"):
        non_climate = [e for e in matches if e["domain"] != "climate"]
        if non_climate:
            matches = non_climate

    # Temperature control path
    target_temp = matcher.parse_temperature(user_text)
    if target_temp and any(e["domain"] == "climate" for e in matches):
        results = []
        for e in (x for x in matches if x["domain"] == "climate"):
            log.info("HA: climate.set_temperature -> %s @ %.1f°", e["entity_id"], target_temp)
            with metrics.timer("ha_call", entity=e["entity_id"]):
                success = client.call("climate", "set_temperature", e["entity_id"],
                                      {"temperature": target_temp})
            results.append({"entity_id": e["entity_id"], "friendly_name": e["friendly_name"],
                            "success": success})
        any_ok = any(r["success"] for r in results)
        return (
            {"action": "set_temp", "entities": results, "room_display": room_display,
             "temperature": target_temp, "error": None if any_ok else "ha_failed"},
            matches,
        )

    if not action:
        return (
            {"action": None, "entities": [], "room_display": room_term,
             "temperature": None, "error": "no_action"},
            [],
        )

    # On/off control path
    results = []
    for e in matches:
        domain = e["domain"]
        if domain == "climate":
            hvac_mode = "heat" if action == "on" else "off"
            log.info("HA: climate.set_hvac_mode(%s) -> %s", hvac_mode, e["entity_id"])
            with metrics.timer("ha_call", entity=e["entity_id"]):
                success = client.call(domain, "set_hvac_mode", e["entity_id"],
                                      {"hvac_mode": hvac_mode})
        else:
            service = f"turn_{action}"
            log.info("HA: %s.%s -> %s", domain, service, e["entity_id"])
            with metrics.timer("ha_call", entity=e["entity_id"]):
                success = client.call(domain, service, e["entity_id"])
        results.append({"entity_id": e["entity_id"], "friendly_name": e["friendly_name"],
                        "success": success})

    any_ok = any(r["success"] for r in results)
    return (
        {"action": action, "entities": results, "room_display": room_display,
         "temperature": None, "error": None if any_ok else "ha_failed"},
        matches,
    )


def control(
    user_text: str,
    *,
    registry: EntityRegistry,
    matcher: EntityMatcher,
    client: HAClient,
    last_entities: list[dict] | None = None,
) -> tuple[str, list[dict]]:
    """Execute + wrap result in Bender TTS.

    Returns (wav_path, matched_entities). Caller stores matched_entities for
    next-turn pronoun resolution.
    """
    result, matched = execute(
        user_text,
        registry=registry,
        matcher=matcher,
        client=client,
        last_entities=last_entities,
    )
    return tts_generate.speak(_result_to_speech(result)), matched


def status(
    user_text: str,
    *,
    registry: EntityRegistry,
    matcher: EntityMatcher,
    last_entities: list[dict] | None = None,
) -> tuple[dict, list[dict]]:
    """Read-only status query — reports cached EntityRegistry state, never
    calls HA (no writes). Cache TTL means the answer can be up to
    ha_entity_cache_ttl_s stale.

    result_dict keys:
        entities    : [{"entity_id", "friendly_name", "domain", "state"}]
        room_display: str
        error       : None | "no_room" | "no_match"
    """
    room_term = matcher.parse_room_term(user_text)

    if not room_term:
        return ({"entities": [], "room_display": "", "error": "no_room"}, [])

    if room_term in PRONOUNS and last_entities:
        matches = last_entities
    else:
        matches = matcher.match(room_term, registry.get())

    if not matches:
        return ({"entities": [], "room_display": room_term, "error": "no_match"}, [])

    room_display = matches[0]["normalised"].title()

    # If both a light/switch and a climate entity matched (e.g. "office"),
    # prefer the non-climate reading unless climate is all that matched —
    # mirrors execute()'s on/off preference.
    non_climate = [e for e in matches if e["domain"] != "climate"]
    if non_climate:
        matches = non_climate

    return (
        {"entities": matches, "room_display": room_display, "error": None},
        matches,
    )


def _status_to_speech(result: dict) -> str:
    error = result.get("error")
    if error == "no_room":
        return random.choice(UNKNOWN_ROOM_RESPONSES)
    if error == "no_match":
        return (f"I can't find anything called {result['room_display']} in Home Assistant. "
                "Try saying the room name differently.")
    lines = []
    for e in result["entities"]:
        is_on = str(e.get("state", "")).lower() in _ON_STATES
        lines.append(f"the {e['friendly_name']} is {'on' if is_on else 'off'}")
    summary = "; ".join(lines) if lines else "I've got nothing on that"
    return random.choice(STATUS_RESPONSES).format(summary=summary[0].upper() + summary[1:])


def report_status(
    user_text: str,
    *,
    registry: EntityRegistry,
    matcher: EntityMatcher,
    last_entities: list[dict] | None = None,
) -> tuple[str, list[dict]]:
    """Execute a read-only status query + wrap in Bender TTS.

    Returns (wav_path, matched_entities), same shape as control().
    """
    result, matched = status(
        user_text,
        registry=registry,
        matcher=matcher,
        last_entities=last_entities,
    )
    return tts_generate.speak(_status_to_speech(result)), matched


# ---------------------------------------------------------------------------
# Convenience factory — build the three objects from cfg once at session start
# ---------------------------------------------------------------------------

def make_default(cfg) -> tuple[EntityRegistry, EntityMatcher, HAClient]:
    client = UrllibHAClient(cfg.ha_url, cfg.ha_token, timeout_s=cfg.http_timeout_s)
    registry = EntityRegistry(
        client,
        exclude_keywords=set(cfg.ha_exclude_keywords),
        exclude_entities=set(cfg.ha_exclude_entities),
        ttl_s=float(cfg.ha_entity_cache_ttl_s),
    )
    matcher = EntityMatcher()
    return registry, matcher, client


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from config import cfg

    _registry, _matcher, _client = make_default(cfg)

    tests = [
        "turn on the office lights",
        "turn off the kitchen lights",
        "bedroom lights off",
        "conservatory lights on",
        "set the office radiator to 20 degrees",
        "turn them off",
    ]
    last: list[dict] = []
    for t in tests:
        result, last = execute(t, registry=_registry, matcher=_matcher,
                               client=_client, last_entities=last)
        print(f"\n  > {t!r}")
        print(f"    action={result['action']!r}, room={result['room_display']!r}, "
              f"error={result['error']!r}")
        print(f"    matched: {[e['entity_id'] for e in last]}")
