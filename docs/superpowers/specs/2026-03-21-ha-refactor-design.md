# Home Assistant Integration Refactor — Design Spec

**Date:** 2026-03-21
**Status:** Draft
**Scope:** Improve HA device matching accuracy and error messaging in `ha_control.py`.

---

## Problem Statement

Three issues with the current Home Assistant integration:

1. **Device matching fails too often** — The matching algorithm uses basic substring search (`term in name`). It fails when the user's spoken words don't appear as an exact substring in the HA entity's friendly name. Common failures: plurals ("lights" vs "light"), word order differences ("office ceiling" vs "Ceiling Office"), abbreviations, and partial names.

2. **Error messages say "HA"** — Three spoken error messages use the abbreviation "HA" instead of "Home Assistant", which sounds robotic and confusing when spoken aloud by TTS.

3. **Silent failures** — When matching fails, Bender says "No idea what X is. Check I have access to it in HA." then the session continues as if nothing happened. The user has no actionable feedback about what went wrong or how to phrase their request differently.

---

## Constraints

- No new pip dependencies — use `difflib.SequenceMatcher` from stdlib
- Must not slow down the happy path (exact match should be instant)
- Matching runs after entity cache fetch (already cached 60s)
- Keep the exclude keywords/entities system — it works
- Future direction: local LLM-assisted matching via AI HAT+ (not in this spec, but the design must not preclude it)

---

## Design

### 1. Token Overlap Scoring (replaces substring matching)

Replace `_find_entities()` substring matching with a two-phase scoring approach:

**Phase 1 — Exact token overlap (fast):**
Split the user's search term and each entity name into word tokens. Score by proportion of user tokens found in the entity name.

```python
def _token_score(user_tokens: set[str], entity_tokens: set[str]) -> float:
    """Fraction of user tokens matched in entity name. 1.0 = perfect."""
    if not user_tokens:
        return 0.0
    return len(user_tokens & entity_tokens) / len(user_tokens)
```

Example: User says "office light" → tokens `{"office"}` (after noise-word stripping)
- Entity "Martin's Office Radiator" → tokens `{"martins", "office", "radiator"}` → overlap `{"office"}` → score 1.0
- Entity "Kitchen Ceiling" → tokens `{"kitchen", "ceiling"}` → overlap `{}` → score 0.0

**Phase 2 — Fuzzy token matching (fallback):**
If no entity scores ≥ 0.5 in Phase 1, fall back to `difflib.SequenceMatcher` on each token pair:

```python
def _fuzzy_token_score(user_tokens: set[str], entity_tokens: set[str]) -> float:
    """Best fuzzy match for each user token against entity tokens."""
    if not user_tokens:
        return 0.0
    total = 0.0
    for ut in user_tokens:
        best = max(
            SequenceMatcher(None, ut, et).ratio()
            for et in entity_tokens
        ) if entity_tokens else 0.0
        total += best
    return total / len(user_tokens)
```

This handles:
- Plurals: "lights" vs "light" → ratio ~0.91
- Abbreviations: "bed" vs "bedroom" → ratio ~0.75
- Typos/mishearing: "kichen" vs "kitchen" → ratio ~0.85

**Scoring thresholds:**
- `≥ 0.8` — high confidence, proceed automatically
- `0.5 – 0.8` — medium confidence, proceed but log a warning
- `< 0.5` — no match, return error

**Domain filtering:**
When the user says "turn on office light", the action word "light" implies domain `light.*`. Use the extracted action/device type to prefer entities from the matching domain:

```python
# If user mentions "light" → prefer light.* entities
# If user mentions "radiator"/"heating" → prefer climate.* entities
# If ambiguous → return all matching entities
```

This prevents "turn on office light" from also matching `climate.office_radiator`.

### 2. Room Synonym Support

Add a synonym map for common room name variations:

```python
ROOM_SYNONYMS = {
    "lounge": "living room",
    "front room": "living room",
    "sitting room": "living room",
    "bedroom": "bed room",
    "bathroom": "bath room",
    "loo": "bathroom",
    "toilet": "bathroom",
    "study": "office",
}
```

Before matching, expand the user's room term through synonyms. This catches "turn on the lounge light" matching an entity named "Living Room Ceiling".

The synonym map should live in `bender_config.json` under `ha_room_synonyms` so it's editable via the web UI without code changes.

### 3. Fix Error Messages

Replace all spoken "HA" references:

| Current | Replacement |
|---|---|
| `"Check I have access to it in HA."` | `"I can't find that device in Home Assistant. Try a different name."` |
| `"HA isn't responding."` | `"Home Assistant isn't responding. Not my fault."` |
| `"HA call failed."` | `"Home Assistant call failed. I blame the humans."` |

Also improve the no-match message to be actionable:

**Current:** `"No idea what {room!r} is. Check I have access to it in HA."`

**New:** `"I can't find anything called {room} in Home Assistant. Try saying the room name differently.""`

### 4. Improved Failure Diagnostics

When matching fails, log the **top 3 closest entities** at DEBUG level so the user can diagnose naming issues:

```python
if not matches:
    closest = sorted(scored, key=lambda x: x[1], reverse=True)[:3]
    log.debug("No match for %r. Closest: %s",
              room_term,
              [(e["friendly_name"], round(s, 2)) for e, s in closest])
    return {"error": "no_match", "room_display": room_term,
            "closest": [(e["friendly_name"], round(s, 2)) for e, s in closest]}
```

The web dashboard could optionally display these diagnostics to help the user understand why matching failed.

### 5. Config Integration

Move HA config reads to use `cfg` singleton (from architecture refactor spec):

- `cfg.ha_url` replaces `os.environ.get("HA_URL")`
- `cfg.ha_token` replaces `os.environ.get("HA_TOKEN")`
- `cfg.ha_exclude_entities` replaces raw `open(bender_config.json)` read
- New: `cfg.ha_room_synonyms` for the synonym map

---

## Files Changed

| File | Changes |
|---|---|
| `scripts/handlers/ha_control.py` | Replace `_find_entities()` with token scoring + fuzzy fallback. Add `_token_score()`, `_fuzzy_token_score()`. Add domain preference filtering. Fix all "HA" string literals. Add diagnostic logging on no-match. Use `cfg` for HA connection params and synonyms. |
| `scripts/config.py` | Add `ha_room_synonyms: dict` and `ha_exclude_entities: list` attributes |
| `bender_config.json` | Add `ha_room_synonyms` map and move `ha_exclude_entities` here |
| `tests/test_ha_control.py` | Add tests for token scoring, fuzzy matching, synonym expansion, domain filtering, error messages |

---

## Testing Strategy

- **Token scoring:** Unit test with known entity names — verify exact matches score 1.0, partial matches score proportionally, misses score 0.0
- **Fuzzy matching:** Test plural handling ("lights" vs "light"), abbreviations ("bed" vs "bedroom"), and typos
- **Synonym expansion:** Test that "lounge" matches "living room" entities
- **Domain filtering:** Test that "turn on office light" prefers `light.office_*` over `climate.office_*`
- **Error messages:** Verify no spoken output contains "HA" abbreviation
- **Diagnostic logging:** Test that no-match logs the top 3 closest entities
- **Regression:** All existing HA control tests must pass unchanged

---

## Future Direction

- **Local LLM matching** — When AI HAT+ local inference is available, add a Phase 3 fallback that sends the user's text + entity list to a local model for disambiguation. The scoring architecture supports this: add a third scoring phase after fuzzy matching, gated by `cfg.ha_use_local_llm`.
- **Entity alias support** — Allow users to define custom aliases ("the big light" → "Living Room Ceiling") in `bender_config.json` via the web UI.
