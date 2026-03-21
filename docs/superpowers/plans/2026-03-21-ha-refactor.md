# HA Integration Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve HA device matching accuracy with fuzzy token scoring, add room synonyms, fix "HA" abbreviation in error messages, add diagnostic logging.

**Architecture:** Replace substring matching in `_find_entities()` with a two-phase scoring approach (exact token overlap → `difflib.SequenceMatcher` fuzzy fallback). Add `ha_room_synonyms` config for natural room name mapping. Fix all spoken "HA" → "Home Assistant".

**Tech Stack:** Python 3.13, difflib (stdlib), pytest

**Spec:** `docs/superpowers/specs/2026-03-21-ha-refactor-design.md`

---

## File Structure

### Modified files

| File | Changes |
|---|---|
| `scripts/handlers/ha_control.py` | Replace `_find_entities()` matching, fix error messages, add diagnostic logging |
| `scripts/config.py` | Add `ha_room_synonyms` attribute |
| `bender_config.json` | Add `ha_room_synonyms` default map |
| `tests/test_ha_control.py` | Expand tests: token scoring, fuzzy matching, synonyms, error messages |

---

## Task 1: Add ha_room_synonyms to config

**Files:**
- Modify: `scripts/config.py`
- Modify: `bender_config.json`
- Modify: `tests/test_config.py`

- [ ] **Step 1: Add test**

Add to `tests/test_config.py`:
```python
def test_ha_room_synonyms_default(self):
    assert isinstance(cfg.ha_room_synonyms, dict)
```

- [ ] **Step 2: Add to config.py**

Add class-level default (after `ha_weather_entity`):
```python
    ha_room_synonyms: dict = {}
```

In `__init__`, after `ha_exclude_entities`, add:
```python
        self.ha_room_synonyms: dict = overrides.get("ha_room_synonyms", {})
```

- [ ] **Step 3: Add default synonyms to bender_config.json**

```json
"ha_room_synonyms": {
    "lounge": "living room",
    "front room": "living room",
    "sitting room": "living room",
    "loo": "bathroom",
    "toilet": "bathroom",
    "study": "office"
}
```

- [ ] **Step 4: Run tests, commit**

```bash
git add scripts/config.py bender_config.json tests/test_config.py
git commit -m "feat: add ha_room_synonyms config for natural room name mapping"
```

---

## Task 2: Replace matching algorithm and fix error messages

**Files:**
- Modify: `scripts/handlers/ha_control.py`
- Modify: `tests/test_ha_control.py`

This is the core task. Read `scripts/handlers/ha_control.py` first.

- [ ] **Step 1: Write comprehensive tests**

Replace `tests/test_ha_control.py` with expanded coverage:

```python
import os
import sys
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from handlers.ha_control import (
    _normalise, _token_score, _fuzzy_token_score,
    _find_entities, _parse_action, _parse_room_term,
    _parse_temperature, _result_to_speech,
)


class TestTokenScore:
    def test_perfect_match(self):
        assert _token_score({"office"}, {"martins", "office", "ceiling"}) == 1.0

    def test_partial_match(self):
        assert _token_score({"office", "ceiling"}, {"martins", "office", "radiator"}) == 0.5

    def test_no_match(self):
        assert _token_score({"kitchen"}, {"office", "ceiling"}) == 0.0

    def test_empty_user(self):
        assert _token_score(set(), {"office"}) == 0.0


class TestFuzzyTokenScore:
    def test_plural_match(self):
        """'lights' should fuzzy-match 'light' with high score."""
        score = _fuzzy_token_score({"lights"}, {"bedroom", "light"})
        assert score > 0.8

    def test_abbreviation_match(self):
        """'bed' should fuzzy-match 'bedroom' reasonably."""
        score = _fuzzy_token_score({"bed"}, {"bedroom", "ceiling"})
        assert score > 0.5

    def test_no_match(self):
        score = _fuzzy_token_score({"kitchen"}, {"office", "radiator"})
        assert score < 0.5


class TestFindEntities:
    def test_exact_match(self):
        entities = [
            {"entity_id": "light.office_ceiling", "attributes": {"friendly_name": "Office Ceiling"}},
            {"entity_id": "light.kitchen", "attributes": {"friendly_name": "Kitchen"}},
        ]
        matches = _find_entities("office", entities)
        assert len(matches) >= 1
        assert any("office" in e["entity_id"] for e in matches)

    def test_no_match_returns_empty(self):
        entities = [
            {"entity_id": "light.kitchen", "attributes": {"friendly_name": "Kitchen"}},
        ]
        matches = _find_entities("garage", entities)
        assert len(matches) == 0


class TestSynonymExpansion:
    @patch("handlers.ha_control.cfg")
    def test_synonym_expands(self, mock_cfg):
        mock_cfg.ha_room_synonyms = {"lounge": "living room"}
        entities = [
            {"entity_id": "light.living_room", "attributes": {"friendly_name": "Living Room Light"}},
        ]
        matches = _find_entities("lounge", entities)
        assert len(matches) >= 1


class TestErrorMessages:
    def test_no_match_says_home_assistant(self):
        """Error messages must say 'Home Assistant', not 'HA'."""
        result = {"error": "no_match", "room_display": "garage"}
        text = _result_to_speech(result)
        assert "HA" not in text or "Home Assistant" in text

    def test_failed_says_home_assistant(self):
        result = {"error": "ha_failed"}
        text = _result_to_speech(result)
        assert "HA" not in text


class TestParseAction:
    def test_on(self):
        assert _parse_action("turn on the kitchen light") == "on"

    def test_off(self):
        assert _parse_action("switch off the bedroom lamp") == "off"

    def test_none(self):
        assert _parse_action("what about the kitchen") is None


class TestParseRoomTerm:
    def test_extracts_room(self):
        room = _parse_room_term("turn on the office light")
        assert "office" in room.lower()

    def test_extracts_kitchen(self):
        room = _parse_room_term("turn off kitchen lights")
        assert "kitchen" in room.lower()


class TestParseTemperature:
    def test_finds_number(self):
        assert _parse_temperature("set temperature to 21 degrees") == 21.0

    def test_no_temp(self):
        assert _parse_temperature("turn on the light") is None
```

- [ ] **Step 2: Run tests to verify some fail**

Run: `cd /c/ws/benderpi && python -m pytest tests/test_ha_control.py -v`
Expected: `_token_score` and `_fuzzy_token_score` fail (don't exist yet)

- [ ] **Step 3: Add token scoring functions**

In `scripts/handlers/ha_control.py`, add after the `_normalise()` function:

```python
from difflib import SequenceMatcher


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
```

- [ ] **Step 4: Replace _find_entities() matching algorithm**

Replace the existing `_find_entities()` function with scored matching:

```python
def _find_entities(room_term: str, entities: list) -> list:
    """Find entities matching room_term using token scoring with fuzzy fallback."""
    # Synonym expansion
    term = room_term.lower().strip()
    term = cfg.ha_room_synonyms.get(term, term)

    term_norm = _normalise(term)
    user_tokens = set(term_norm.split())

    scored = []
    for entity in entities:
        name = entity.get("attributes", {}).get("friendly_name", "")
        eid = entity.get("entity_id", "")
        name_norm = _normalise(name)
        id_norm = _normalise(eid.replace(".", " "))
        entity_tokens = set(name_norm.split()) | set(id_norm.split())

        # Phase 1: exact token overlap
        score = _token_score(user_tokens, entity_tokens)
        scored.append((entity, score))

    # If no good exact matches, try fuzzy
    best_exact = max((s for _, s in scored), default=0.0)
    if best_exact < 0.5:
        scored = []
        for entity in entities:
            name = entity.get("attributes", {}).get("friendly_name", "")
            eid = entity.get("entity_id", "")
            name_norm = _normalise(name)
            id_norm = _normalise(eid.replace(".", " "))
            entity_tokens = set(name_norm.split()) | set(id_norm.split())
            score = _fuzzy_token_score(user_tokens, entity_tokens)
            scored.append((entity, score))

    # Filter by threshold and return
    threshold = 0.5
    matches = [(e, s) for e, s in scored if s >= threshold]
    matches.sort(key=lambda x: x[1], reverse=True)

    if not matches:
        # Diagnostic logging — top 3 closest
        closest = sorted(scored, key=lambda x: x[1], reverse=True)[:3]
        log.debug("No match for %r. Closest: %s",
                  room_term,
                  [(e.get("attributes", {}).get("friendly_name", "?"), round(s, 2))
                   for e, s in closest])
        return []

    return [e for e, s in matches]
```

- [ ] **Step 5: Fix error messages**

In `_result_to_speech()`, replace all "HA" references:

1. `"No idea what {room_display!r} is. Check I have access to it in HA."` →
   `"I can't find anything called {room_display} in Home Assistant. Try saying the room name differently."`

2. In `FAILED_RESPONSES`, replace:
   - `"Something went wrong. HA isn't responding. Probably not my fault."` →
     `"Something went wrong. Home Assistant isn't responding. Probably not my fault."`
   - `"HA call failed. I blame the humans who built this infrastructure."` →
     `"Home Assistant call failed. I blame the humans who built this infrastructure."`

- [ ] **Step 6: Run all tests**

Run: `cd /c/ws/benderpi && python -m pytest tests/ -v`
Expected: all tests PASS

- [ ] **Step 7: Commit**

```bash
git add scripts/handlers/ha_control.py tests/test_ha_control.py
git commit -m "refactor: HA matching — token scoring, fuzzy fallback, synonyms, fix error messages"
```
