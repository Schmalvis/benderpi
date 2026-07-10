"""Tests for the HA_STATUS read-only path (Fable review group #9):

- entity_matcher.EntityMatcher.parse_action(allow_bare=...)
- ha_control.status() / report_status() / _status_to_speech()
- ha_control.execute() defense-in-depth against bare on/off on question text

These never issue a real HA write call (no client.call for the status path;
execute() must not infer an action from a bare on/off on question-shaped text).
"""
import inspect
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from handlers.entity_matcher import EntityMatcher
from handlers.ha_control import execute, report_status, status, _status_to_speech


def _entity(entity_id, domain, friendly_name, state="off", normalised=None):
    return {
        "entity_id": entity_id,
        "domain": domain,
        "friendly_name": friendly_name,
        "normalised": normalised or friendly_name.lower(),
        "state": state,
    }


OFFICE_LIGHT_ON = _entity("light.office_ceiling", "light", "Office Ceiling",
                          state="on", normalised="office ceiling")
KITCHEN_LIGHT_OFF = _entity("light.kitchen", "light", "Kitchen", state="off",
                            normalised="kitchen")
OFFICE_RADIATOR_HEAT = _entity("climate.office_radiator", "climate", "Office Radiator",
                               state="heat", normalised="office radiator")


def _matcher(room_term=None, matches=None):
    m = MagicMock()
    m.parse_room_term.return_value = room_term
    m.match.return_value = matches or []
    return m


class TestParseActionAllowBare:
    def test_bare_off_allowed_by_default(self):
        assert EntityMatcher.parse_action("bedroom lights off") == "off"

    def test_bare_off_suppressed_when_disallowed(self):
        assert EntityMatcher.parse_action("bedroom lights off", allow_bare=False) is None

    def test_bare_on_suppressed_when_disallowed(self):
        assert EntityMatcher.parse_action("is the office light on", allow_bare=False) is None

    def test_explicit_phrase_still_works_when_bare_disallowed(self):
        assert EntityMatcher.parse_action("turn off the bedroom lights", allow_bare=False) == "off"
        assert EntityMatcher.parse_action("turn on the bedroom lights", allow_bare=False) == "on"


class TestStatusReadOnly:
    def test_no_room(self):
        matcher = _matcher(room_term=None)
        result, matched = status("what's up", registry=MagicMock(), matcher=matcher)
        assert result["error"] == "no_room"
        assert matched == []

    def test_no_match(self):
        matcher = _matcher(room_term="garage", matches=[])
        registry = MagicMock()
        registry.get.return_value = [OFFICE_LIGHT_ON]
        result, matched = status("is the garage light on", registry=registry, matcher=matcher)
        assert result["error"] == "no_match"
        assert result["room_display"] == "garage"

    def test_happy_path_reports_cached_state(self):
        matcher = _matcher(room_term="office", matches=[OFFICE_LIGHT_ON])
        registry = MagicMock()
        registry.get.return_value = [OFFICE_LIGHT_ON]
        result, matched = status("is the office light on", registry=registry, matcher=matcher)
        assert result["error"] is None
        assert result["entities"] == [OFFICE_LIGHT_ON]
        assert matched == [OFFICE_LIGHT_ON]

    def test_prefers_non_climate_when_mixed_matches(self):
        matcher = _matcher(room_term="office", matches=[OFFICE_LIGHT_ON, OFFICE_RADIATOR_HEAT])
        registry = MagicMock()
        registry.get.return_value = [OFFICE_LIGHT_ON, OFFICE_RADIATOR_HEAT]
        result, matched = status("is the office on", registry=registry, matcher=matcher)
        assert [e["entity_id"] for e in result["entities"]] == ["light.office_ceiling"]

    def test_pronoun_resolution_uses_last_entities_without_matching(self):
        matcher = _matcher(room_term="it")
        result, matched = status(
            "is it on", registry=MagicMock(), matcher=matcher,
            last_entities=[KITCHEN_LIGHT_OFF],
        )
        matcher.match.assert_not_called()
        assert matched == [KITCHEN_LIGHT_OFF]

    def test_status_takes_no_client_and_cannot_write(self):
        """Safety property: status() has no client parameter at all, so it is
        structurally incapable of issuing an HA write call."""
        assert "client" not in inspect.signature(status).parameters


class TestStatusToSpeech:
    def test_reports_on_state(self):
        text = _status_to_speech({"error": None, "entities": [OFFICE_LIGHT_ON]})
        assert "office ceiling" in text.lower()
        assert "on" in text.lower()

    def test_reports_off_state(self):
        text = _status_to_speech({"error": None, "entities": [KITCHEN_LIGHT_OFF]})
        assert "off" in text.lower()

    def test_climate_heat_state_reads_as_on(self):
        text = _status_to_speech({"error": None, "entities": [OFFICE_RADIATOR_HEAT]})
        assert "on" in text.lower()

    def test_no_room_asks_which_room(self):
        text = _status_to_speech({"error": "no_room"})
        assert "room" in text.lower()

    def test_no_match_names_the_room(self):
        text = _status_to_speech({"error": "no_match", "room_display": "garage"})
        assert "garage" in text


class TestReportStatus:
    def test_wraps_result_in_tts(self):
        matcher = _matcher(room_term="office", matches=[OFFICE_LIGHT_ON])
        registry = MagicMock()
        registry.get.return_value = [OFFICE_LIGHT_ON]
        with patch("handlers.ha_control.tts_generate.speak", return_value="/tmp/status.wav") as mock_speak:
            wav_path, matched = report_status(
                "is the office light on", registry=registry, matcher=matcher,
            )
        assert wav_path == "/tmp/status.wav"
        assert matched == [OFFICE_LIGHT_ON]
        mock_speak.assert_called_once()


class TestExecuteDefenseInDepth:
    """If intent classification ever mis-routes a question into HA_CONTROL's
    execute() (defense in depth, not the primary guard), the bare on/off
    fallback must not fire and no HA call should happen. Uses the real
    EntityMatcher (not a mock) to exercise the actual allow_bare wiring."""

    def test_bare_on_off_suppressed_for_question_text(self):
        matcher = EntityMatcher()
        registry = MagicMock()
        registry.get.return_value = [OFFICE_LIGHT_ON]
        client = MagicMock()
        result, matched = execute(
            "is the office light on", registry=registry, matcher=matcher, client=client,
        )
        assert result["action"] is None
        assert result["error"] == "no_action"
        client.call.assert_not_called()

    def test_bare_on_off_still_works_for_imperatives(self):
        matcher = EntityMatcher()
        registry = MagicMock()
        registry.get.return_value = [OFFICE_LIGHT_ON]
        client = MagicMock()
        client.call.return_value = True
        result, matched = execute(
            "office light on", registry=registry, matcher=matcher, client=client,
        )
        assert result["action"] == "on"
        client.call.assert_called_once_with("light", "turn_on", "light.office_ceiling")

    def test_explicit_turn_off_still_works_on_narration_text(self):
        """'been turned off' is narration, but if it somehow reached execute(),
        an explicit 'turn off' elsewhere in the sentence should still count —
        only the bare fallback is suppressed, not the explicit-phrase path."""
        matcher = EntityMatcher()
        registry = MagicMock()
        registry.get.return_value = [OFFICE_LIGHT_ON]
        client = MagicMock()
        client.call.return_value = True
        result, matched = execute(
            "turn off the office light, it's been turned off before",
            registry=registry, matcher=matcher, client=client,
        )
        assert result["action"] == "off"
