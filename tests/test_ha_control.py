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
        score = _fuzzy_token_score({"lights"}, {"bedroom", "light"})
        assert score > 0.8

    def test_abbreviation_match(self):
        score = _fuzzy_token_score({"bed"}, {"bedroom", "ceiling"})
        assert score > 0.5

    def test_no_match(self):
        score = _fuzzy_token_score({"kitchen"}, {"office", "radiator"})
        assert score < 0.5


class TestFindEntities:
    @patch("handlers.ha_control.cfg")
    def test_exact_match(self, mock_cfg):
        mock_cfg.ha_room_synonyms = {}
        entities = [
            {"entity_id": "light.office_ceiling", "attributes": {"friendly_name": "Office Ceiling"}},
            {"entity_id": "light.kitchen", "attributes": {"friendly_name": "Kitchen"}},
        ]
        matches = _find_entities("office", entities)
        assert len(matches) >= 1
        assert any("office" in e["entity_id"] for e in matches)

    @patch("handlers.ha_control.cfg")
    def test_no_match_returns_empty(self, mock_cfg):
        mock_cfg.ha_room_synonyms = {}
        entities = [
            {"entity_id": "light.kitchen", "attributes": {"friendly_name": "Kitchen"}},
        ]
        matches = _find_entities("garage", entities)
        assert len(matches) == 0

    @patch("handlers.ha_control.cfg")
    def test_synonym_expansion(self, mock_cfg):
        mock_cfg.ha_room_synonyms = {"lounge": "living room"}
        entities = [
            {"entity_id": "light.living_room", "attributes": {"friendly_name": "Living Room Light"}},
        ]
        matches = _find_entities("lounge", entities)
        assert len(matches) >= 1


class TestErrorMessages:
    def test_no_match_says_home_assistant(self):
        result = {"error": "no_match", "room_display": "garage"}
        text = _result_to_speech(result)
        assert "Home Assistant" in text
        # Should NOT contain bare "HA" (but "Home Assistant" contains "HA" — check no standalone HA)
        text_without_ha_full = text.replace("Home Assistant", "")
        assert " HA " not in text_without_ha_full
        assert " HA." not in text_without_ha_full

    def test_failed_says_home_assistant(self):
        result = {"error": "ha_failed"}
        text = _result_to_speech(result)
        text_without_ha_full = text.replace("Home Assistant", "")
        assert " HA " not in text_without_ha_full
        assert " HA." not in text_without_ha_full


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
        assert room is not None
        assert "office" in room.lower()

    def test_extracts_kitchen(self):
        room = _parse_room_term("turn off kitchen lights")
        assert room is not None
        assert "kitchen" in room.lower()


class TestParseTemperature:
    def test_finds_number(self):
        assert _parse_temperature("set temperature to 21 degrees") == 21.0

    def test_no_temp(self):
        assert _parse_temperature("turn on the light") is None
