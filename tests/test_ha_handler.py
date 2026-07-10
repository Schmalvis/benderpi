"""Tests for HAHandler — dispatches HA_CONTROL to ha_control.control() (writes)
and HA_STATUS to ha_control.report_status() (read-only)."""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))


def test_handle_success():
    """Returns a Response when ha_control.control returns a WAV path."""
    with patch("handlers.ha_control.control", return_value=("/tmp/ha_response.wav", [])):
        from handlers.ha_handler import HAHandler

        handler = HAHandler()
        result = handler.handle("turn on the living room light", "HA_CONTROL")

        assert result is not None
        assert result.wav_path == "/tmp/ha_response.wav"
        assert result.method == "handler_ha"
        assert result.intent == "HA_CONTROL"
        assert result.text == "turn on the living room light"


def test_handle_failure():
    """Returns None when ha_control.control returns no WAV."""
    with patch("handlers.ha_control.control", return_value=(None, [])):
        from handlers.ha_handler import HAHandler

        handler = HAHandler()
        result = handler.handle("turn on the living room light", "HA_CONTROL")

        assert result is None


def test_handle_passes_sub_key():
    """sub_key is forwarded to the Response."""
    with patch("handlers.ha_control.control", return_value=("/tmp/ha_response.wav", [])):
        from handlers.ha_handler import HAHandler

        handler = HAHandler()
        result = handler.handle("turn off the fan", "HA_CONTROL", sub_key="light")

        assert result is not None
        assert result.sub_key == "light"


def test_handle_text_passed_to_control():
    """The full user text is passed to ha_control.control, along with the
    handler's registry/matcher/client and pronoun-resolution state."""
    with patch("handlers.ha_control.control", return_value=("/tmp/ha_response.wav", [])) as mock_control:
        from handlers.ha_handler import HAHandler

        handler = HAHandler()
        handler.handle("set the thermostat to 21 degrees", "HA_CONTROL")

        mock_control.assert_called_once_with(
            "set the thermostat to 21 degrees",
            registry=handler._registry,
            matcher=handler._matcher,
            client=handler._client,
            last_entities=[],
        )


def test_intents_declaration():
    """HAHandler declares both the write (HA_CONTROL) and read-only
    (HA_STATUS) intents."""
    from handlers.ha_handler import HAHandler

    assert "HA_CONTROL" in HAHandler.intents
    assert "HA_STATUS" in HAHandler.intents


class TestHAStatusRouting:
    def test_status_intent_calls_report_status_not_control(self):
        """HA_STATUS must go through the read-only path, never ha_control.control
        (which issues real HA write calls)."""
        with patch("handlers.ha_control.report_status",
                   return_value=("/tmp/status.wav", [])) as mock_status, \
             patch("handlers.ha_control.control") as mock_control:
            from handlers.ha_handler import HAHandler

            handler = HAHandler()
            result = handler.handle("is the office light on", "HA_STATUS")

            mock_status.assert_called_once()
            mock_control.assert_not_called()
            assert result is not None
            assert result.wav_path == "/tmp/status.wav"
            assert result.method == "handler_ha_status"
            assert result.intent == "HA_STATUS"

    def test_status_passes_text_and_state(self):
        with patch("handlers.ha_control.report_status",
                   return_value=("/tmp/status.wav", [])) as mock_status:
            from handlers.ha_handler import HAHandler

            handler = HAHandler()
            handler.handle("is the office light on", "HA_STATUS")

            mock_status.assert_called_once_with(
                "is the office light on",
                registry=handler._registry,
                matcher=handler._matcher,
                last_entities=[],
            )

    def test_status_failure_returns_none(self):
        with patch("handlers.ha_control.report_status", return_value=(None, [])):
            from handlers.ha_handler import HAHandler

            handler = HAHandler()
            result = handler.handle("is the office light on", "HA_STATUS")

            assert result is None

    def test_status_updates_last_entities_for_pronoun_resolution(self):
        entity = {"entity_id": "light.office_ceiling", "domain": "light",
                  "friendly_name": "Office Ceiling", "normalised": "office ceiling"}
        with patch("handlers.ha_control.report_status",
                   return_value=("/tmp/status.wav", [entity])):
            from handlers.ha_handler import HAHandler

            handler = HAHandler()
            handler.handle("is the office light on", "HA_STATUS")

            assert handler._last_entities == [entity]
