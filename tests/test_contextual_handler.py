import os
import sys
import types
from unittest.mock import patch, MagicMock
from datetime import datetime

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from handlers.contextual_handler import ContextualHandler


class TestContextualTime:
    @patch("handlers.contextual_handler.tts_generate")
    def test_time_returns_response(self, mock_tts):
        mock_tts.speak.return_value = "/tmp/test.wav"
        h = ContextualHandler()
        resp = h.handle("what time is it", "CONTEXTUAL", sub_key="time")
        assert resp is not None
        assert resp.method == "handler_contextual"
        assert resp.intent == "CONTEXTUAL"
        assert resp.sub_key == "time"
        assert resp.is_temp is True
        assert resp.needs_thinking is True
        mock_tts.speak.assert_called_once()
        spoken_text = mock_tts.speak.call_args[0][0]
        assert any(c.isdigit() for c in spoken_text)

    @patch("handlers.contextual_handler.tts_generate")
    def test_time_contains_am_or_pm(self, mock_tts):
        mock_tts.speak.return_value = "/tmp/test.wav"
        h = ContextualHandler()
        resp = h.handle("what time is it", "CONTEXTUAL", sub_key="time")
        spoken = mock_tts.speak.call_args[0][0]
        assert "AM" in spoken.upper() or "PM" in spoken.upper() or ":" in spoken


class TestContextualDate:
    @patch("handlers.contextual_handler.tts_generate")
    def test_date_returns_response(self, mock_tts):
        mock_tts.speak.return_value = "/tmp/test.wav"
        h = ContextualHandler()
        resp = h.handle("what's the date", "CONTEXTUAL", sub_key="date")
        assert resp is not None
        assert resp.method == "handler_contextual"
        assert resp.sub_key == "date"
        mock_tts.speak.assert_called_once()


class TestContextualStatus:
    @patch("handlers.contextual_handler.tts_generate")
    @patch("handlers.contextual_handler._get_cpu_temp", return_value="42°C")
    @patch("handlers.contextual_handler._get_uptime", return_value="3 hours")
    @patch("handlers.contextual_handler._get_session_count", return_value=5)
    def test_status_returns_response(self, mock_sessions, mock_uptime, mock_temp, mock_tts):
        mock_tts.speak.return_value = "/tmp/test.wav"
        h = ContextualHandler()
        resp = h.handle("how are you doing", "CONTEXTUAL", sub_key="status")
        assert resp is not None
        assert resp.sub_key == "status"
        spoken = mock_tts.speak.call_args[0][0]
        assert "42" in spoken
        assert "3 hours" in spoken


class TestContextualUnknownSubKey:
    def test_unknown_sub_key_returns_none(self):
        h = ContextualHandler()
        resp = h.handle("something", "CONTEXTUAL", sub_key="unknown")
        assert resp is None
