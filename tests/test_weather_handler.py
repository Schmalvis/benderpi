"""Tests for WeatherHandler."""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest


def test_handle_success():
    """Returns a Response when briefings returns a WAV path."""
    with patch("briefings.get_weather_wav", return_value="/tmp/weather.wav"):
        from handlers.weather_handler import WeatherHandler

        handler = WeatherHandler()
        result = handler.handle("what's the weather", "WEATHER")

        assert result is not None
        assert result.wav_path == "/tmp/weather.wav"
        assert result.method == "handler_weather"
        assert result.intent == "WEATHER"
        assert result.text == "weather briefing"


def test_handle_failure():
    """Returns None when briefings returns None (no WAV available)."""
    with patch("briefings.get_weather_wav", return_value=None):
        from handlers.weather_handler import WeatherHandler

        handler = WeatherHandler()
        result = handler.handle("what's the weather", "WEATHER")

        assert result is None


def test_handle_passes_sub_key():
    """sub_key is forwarded to the Response."""
    with patch("briefings.get_weather_wav", return_value="/tmp/weather.wav"):
        from handlers.weather_handler import WeatherHandler

        handler = WeatherHandler()
        result = handler.handle("weather", "WEATHER", sub_key="forecast")

        assert result is not None
        assert result.sub_key == "forecast"


def test_intents_declaration():
    """WeatherHandler declares the WEATHER intent."""
    from handlers.weather_handler import WeatherHandler

    assert "WEATHER" in WeatherHandler.intents
