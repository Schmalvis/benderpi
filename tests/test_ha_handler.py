"""Tests for HAHandler."""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest


def test_handle_success():
    """Returns a Response when ha_control.control returns a WAV path."""
    with patch("handlers.ha_control.control", return_value="/tmp/ha_response.wav"):
        from handlers.ha_handler import HAHandler

        handler = HAHandler()
        result = handler.handle("turn on the living room light", "HA_CONTROL")

        assert result is not None
        assert result.wav_path == "/tmp/ha_response.wav"
        assert result.method == "handler_ha"
        assert result.intent == "HA_CONTROL"
        assert result.text == "turn on the living room light"


def test_handle_failure():
    """Returns None when ha_control.control returns None."""
    with patch("handlers.ha_control.control", return_value=None):
        from handlers.ha_handler import HAHandler

        handler = HAHandler()
        result = handler.handle("turn on the living room light", "HA_CONTROL")

        assert result is None


def test_handle_passes_sub_key():
    """sub_key is forwarded to the Response."""
    with patch("handlers.ha_control.control", return_value="/tmp/ha_response.wav"):
        from handlers.ha_handler import HAHandler

        handler = HAHandler()
        result = handler.handle("turn off the fan", "HA_CONTROL", sub_key="light")

        assert result is not None
        assert result.sub_key == "light"


def test_handle_text_passed_to_control():
    """The full user text is passed to ha_control.control."""
    with patch("handlers.ha_control.control", return_value="/tmp/ha_response.wav") as mock_control:
        from handlers.ha_handler import HAHandler

        handler = HAHandler()
        handler.handle("set the thermostat to 21 degrees", "HA_CONTROL")

        mock_control.assert_called_once_with("set the thermostat to 21 degrees")


def test_intents_declaration():
    """HAHandler declares the HA_CONTROL intent."""
    from handlers.ha_handler import HAHandler

    assert "HA_CONTROL" in HAHandler.intents
