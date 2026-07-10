"""Tests for handlers/ha_control.py — orchestration (execute/control) against
mocked EntityRegistry/EntityMatcher/HAClient collaborators, plus the pure
_result_to_speech() template rendering.

Entity-matching logic itself (normalise/_token_score/_fuzzy_score/EntityMatcher)
is covered separately in tests/test_entity_matcher.py.
"""
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from handlers.ha_control import execute, control, _result_to_speech, FAILED_RESPONSES


def _entity(entity_id, domain, friendly_name, normalised=None):
    return {
        "entity_id": entity_id,
        "domain": domain,
        "friendly_name": friendly_name,
        "normalised": normalised or friendly_name.lower(),
    }


OFFICE_LIGHT = _entity("light.office_ceiling", "light", "Office Ceiling", "office ceiling")
OFFICE_RADIATOR = _entity("climate.office_radiator", "climate", "Office Radiator", "office radiator")


def _matcher(action=None, room_term=None, matches=None, temperature=None):
    """Build a MagicMock matcher with canned parse_*/match return values."""
    m = MagicMock()
    m.parse_action.return_value = action
    m.parse_room_term.return_value = room_term
    m.match.return_value = matches or []
    m.parse_temperature.return_value = temperature
    return m


def _client(call_result=True):
    c = MagicMock()
    c.call.return_value = call_result
    return c


class TestExecuteErrors:
    def test_no_room_when_room_term_is_none(self):
        matcher = _matcher(action="on", room_term=None)
        result, matched = execute(
            "what's up", registry=MagicMock(), matcher=matcher, client=_client(),
        )
        assert result["error"] == "no_room"
        assert matched == []

    def test_no_match_when_matcher_finds_nothing(self):
        matcher = _matcher(action="on", room_term="garage", matches=[])
        registry = MagicMock()
        registry.get.return_value = [OFFICE_LIGHT]
        result, matched = execute(
            "turn on the garage light", registry=registry, matcher=matcher, client=_client(),
        )
        assert result["error"] == "no_match"
        assert result["room_display"] == "garage"
        assert matched == []

    def test_no_action_when_action_is_none(self):
        matcher = _matcher(action=None, room_term="office", matches=[OFFICE_LIGHT])
        registry = MagicMock()
        registry.get.return_value = [OFFICE_LIGHT]
        result, matched = execute(
            "the office", registry=registry, matcher=matcher, client=_client(),
        )
        assert result["error"] == "no_action"

    def test_ha_failed_when_client_call_fails(self):
        matcher = _matcher(action="on", room_term="office", matches=[OFFICE_LIGHT])
        registry = MagicMock()
        registry.get.return_value = [OFFICE_LIGHT]
        result, matched = execute(
            "turn on the office light", registry=registry, matcher=matcher,
            client=_client(call_result=False),
        )
        assert result["error"] == "ha_failed"
        assert result["entities"][0]["success"] is False


class TestExecuteOnOff:
    def test_on_off_calls_client_turn_service_per_entity(self):
        matcher = _matcher(action="on", room_term="office", matches=[OFFICE_LIGHT])
        registry = MagicMock()
        registry.get.return_value = [OFFICE_LIGHT]
        client = _client(call_result=True)
        result, matched = execute(
            "turn on the office light", registry=registry, matcher=matcher, client=client,
        )
        assert result["error"] is None
        assert result["action"] == "on"
        assert result["room_display"] == "Office Ceiling"
        client.call.assert_called_once_with("light", "turn_on", "light.office_ceiling")
        assert matched == [OFFICE_LIGHT]

    def test_off_action_uses_turn_off_service(self):
        matcher = _matcher(action="off", room_term="office", matches=[OFFICE_LIGHT])
        registry = MagicMock()
        registry.get.return_value = [OFFICE_LIGHT]
        client = _client(call_result=True)
        execute("turn off the office light", registry=registry, matcher=matcher, client=client)
        client.call.assert_called_once_with("light", "turn_off", "light.office_ceiling")

    def test_climate_entity_uses_set_hvac_mode(self):
        matcher = _matcher(action="on", room_term="radiator", matches=[OFFICE_RADIATOR])
        registry = MagicMock()
        registry.get.return_value = [OFFICE_RADIATOR]
        client = _client(call_result=True)
        execute("turn on the radiator", registry=registry, matcher=matcher, client=client)
        client.call.assert_called_once_with(
            "climate", "set_hvac_mode", "climate.office_radiator", {"hvac_mode": "heat"},
        )

    def test_mixed_matches_prefer_non_climate_for_on_off(self):
        matcher = _matcher(action="on", room_term="office", matches=[OFFICE_LIGHT, OFFICE_RADIATOR])
        registry = MagicMock()
        registry.get.return_value = [OFFICE_LIGHT, OFFICE_RADIATOR]
        client = _client(call_result=True)
        result, matched = execute(
            "turn on the office", registry=registry, matcher=matcher, client=client,
        )
        # Only the non-climate entity should have been acted on.
        assert [e["entity_id"] for e in result["entities"]] == ["light.office_ceiling"]
        client.call.assert_called_once_with("light", "turn_on", "light.office_ceiling")


class TestExecuteTemperature:
    def test_set_temp_calls_climate_set_temperature(self):
        matcher = _matcher(action=None, room_term="radiator", matches=[OFFICE_RADIATOR], temperature=21.0)
        registry = MagicMock()
        registry.get.return_value = [OFFICE_RADIATOR]
        client = _client(call_result=True)
        result, matched = execute(
            "set the radiator to 21 degrees", registry=registry, matcher=matcher, client=client,
        )
        assert result["action"] == "set_temp"
        assert result["temperature"] == 21.0
        assert result["error"] is None
        client.call.assert_called_once_with(
            "climate", "set_temperature", "climate.office_radiator", {"temperature": 21.0},
        )

    def test_set_temp_ignored_without_climate_match(self):
        # Temperature parsed but only a light entity matched -> falls through
        # to the on/off path, which has no action -> no_action error.
        matcher = _matcher(action=None, room_term="office", matches=[OFFICE_LIGHT], temperature=21.0)
        registry = MagicMock()
        registry.get.return_value = [OFFICE_LIGHT]
        client = _client(call_result=True)
        result, matched = execute(
            "set the office to 21 degrees", registry=registry, matcher=matcher, client=client,
        )
        assert result["action"] is None
        assert result["error"] == "no_action"
        client.call.assert_not_called()


class TestPronounResolution:
    def test_pronoun_uses_last_entities_without_calling_match(self):
        matcher = _matcher(action="off", room_term="them")
        registry = MagicMock()
        client = _client(call_result=True)
        result, matched = execute(
            "turn them off", registry=registry, matcher=matcher, client=client,
            last_entities=[OFFICE_LIGHT],
        )
        matcher.match.assert_not_called()
        assert matched == [OFFICE_LIGHT]
        assert result["error"] is None

    def test_pronoun_without_last_entities_falls_back_to_match(self):
        matcher = _matcher(action="off", room_term="it", matches=[])
        registry = MagicMock()
        registry.get.return_value = []
        client = _client(call_result=True)
        result, matched = execute(
            "turn it off", registry=registry, matcher=matcher, client=client,
            last_entities=None,
        )
        matcher.match.assert_called_once()
        assert result["error"] == "no_match"


class TestControl:
    def test_control_wraps_result_in_tts(self):
        matcher = _matcher(action="on", room_term="office", matches=[OFFICE_LIGHT])
        registry = MagicMock()
        registry.get.return_value = [OFFICE_LIGHT]
        client = _client(call_result=True)
        with patch("handlers.ha_control.tts_generate.speak", return_value="/tmp/ha.wav") as mock_speak:
            wav_path, matched = control(
                "turn on the office light", registry=registry, matcher=matcher, client=client,
            )
        assert wav_path == "/tmp/ha.wav"
        assert matched == [OFFICE_LIGHT]
        mock_speak.assert_called_once()
        assert "Office Ceiling" in mock_speak.call_args[0][0]


class TestResultToSpeech:
    def test_no_room_asks_which_room(self):
        text = _result_to_speech({"error": "no_room"})
        assert "room" in text.lower()

    def test_no_match_names_the_room(self):
        text = _result_to_speech({"error": "no_match", "room_display": "garage"})
        assert "garage" in text

    def test_no_action_asks_for_clarity(self):
        text = _result_to_speech({"error": "no_action"})
        assert text

    def test_ha_failed_is_one_of_the_known_failure_lines(self):
        text = _result_to_speech({"error": "ha_failed"})
        assert text in FAILED_RESPONSES

    def test_on_action_mentions_room(self):
        text = _result_to_speech({"error": None, "action": "on", "room_display": "Office"})
        assert "Office" in text

    def test_off_action_mentions_room(self):
        text = _result_to_speech({"error": None, "action": "off", "room_display": "Kitchen"})
        assert "Kitchen" in text

    def test_set_temp_mentions_degrees_and_room(self):
        text = _result_to_speech({
            "error": None, "action": "set_temp", "room_display": "Office", "temperature": 21.0,
        })
        assert "21" in text
        assert "Office" in text
