"""Tests for scenario classifier and hybrid AI routing in Responder."""
import sys
from unittest.mock import MagicMock

# anthropic is not installed in the test environment; stub it before any import
sys.modules.setdefault("anthropic", MagicMock())

import pytest
import requests
from unittest.mock import patch
from responder import Responder
from handler_base import Response
from ai_local import QualityCheckFailed


class TestClassifyScenario:
    @pytest.fixture
    def responder(self):
        with patch("responder.Responder.__init__", return_value=None):
            r = Responder.__new__(Responder)
            r._dispatch = {}
            return r

    @pytest.mark.parametrize("text,expected", [
        ("who invented penicillin", "knowledge"),
        ("what year did the war end", "knowledge"),
        ("where is the Eiffel Tower", "knowledge"),
        ("how many planets are there", "knowledge"),
        ("explain quantum physics", "knowledge"),
        ("what is the capital of France", "knowledge"),
    ])
    def test_knowledge_queries(self, responder, text, expected):
        assert responder._classify_scenario(text) == expected

    @pytest.mark.parametrize("text,expected", [
        ("tell me a joke", "creative"),
        ("sing me a song", "creative"),
        ("insult me", "creative"),
        ("roast my friend", "creative"),
        ("tell me a story", "creative"),
    ])
    def test_creative_queries(self, responder, text, expected):
        assert responder._classify_scenario(text) == expected

    @pytest.mark.parametrize("text,expected", [
        ("what do you think of humans", "conversation"),
        ("how are you feeling today", "conversation"),
        ("do you like beer", "conversation"),
        ("you're the best robot", "conversation"),
    ])
    def test_conversation_queries(self, responder, text, expected):
        assert responder._classify_scenario(text) == expected


class TestRespondAIRouting:
    @pytest.fixture
    def responder(self):
        with patch("responder.Responder.__init__", return_value=None):
            r = Responder.__new__(Responder)
            r._dispatch = {}
            return r

    def _mock_cloud_ai(self, reply="Cloud says hi, meatbag!"):
        ai = MagicMock()
        ai.respond.return_value = "/tmp/cloud.wav"
        ai.history = [{"role": "assistant", "content": reply}]
        return ai

    def _mock_local_ai(self, reply="Local says bite my shiny metal ass!"):
        ai = MagicMock()
        ai.generate.return_value = reply
        return ai

    @patch("responder.tts_generate")
    @patch("responder.cfg")
    def test_local_first_success(self, mock_cfg, mock_tts, responder):
        mock_cfg.ai_backend = "hybrid"
        mock_cfg.ai_routing = {"conversation": "local_first"}
        mock_cfg.ai_model = "claude-haiku"
        mock_tts.speak.return_value = "/tmp/local.wav"

        ai_cloud = self._mock_cloud_ai()
        ai_local = self._mock_local_ai()

        resp = responder._respond_ai("hello bender", ai_cloud,
                                     "UNKNOWN", None, ai_local)
        assert resp.method == "ai_local"
        ai_local.generate.assert_called_once()
        ai_cloud.respond.assert_not_called()

    @patch("responder.tts_generate")
    @patch("responder.cfg")
    def test_local_first_escalates_on_quality_failure(self, mock_cfg, mock_tts, responder):
        mock_cfg.ai_backend = "hybrid"
        mock_cfg.ai_routing = {"conversation": "local_first"}
        mock_cfg.ai_model = "claude-haiku"
        mock_tts.speak.return_value = "/tmp/cloud.wav"

        ai_cloud = self._mock_cloud_ai()
        ai_local = MagicMock()
        ai_local.generate.side_effect = QualityCheckFailed("hedge_phrase", "I don't know")

        resp = responder._respond_ai("who was the first king", ai_cloud,
                                     "UNKNOWN", None, ai_local)
        assert resp.method == "ai_fallback"
        ai_cloud.respond.assert_called_once()

    @patch("responder.tts_generate")
    @patch("responder.cfg")
    def test_local_first_escalates_on_timeout(self, mock_cfg, mock_tts, responder):
        mock_cfg.ai_backend = "hybrid"
        mock_cfg.ai_routing = {"conversation": "local_first"}
        mock_cfg.ai_model = "claude-haiku"
        mock_tts.speak.return_value = "/tmp/cloud.wav"

        ai_cloud = self._mock_cloud_ai()
        ai_local = MagicMock()
        ai_local.generate.side_effect = requests.exceptions.Timeout()

        resp = responder._respond_ai("tell me something", ai_cloud,
                                     "UNKNOWN", None, ai_local)
        assert resp.method == "ai_fallback"

    @patch("responder.cfg")
    def test_cloud_only_skips_local(self, mock_cfg, responder):
        mock_cfg.ai_backend = "cloud_only"
        mock_cfg.ai_model = "claude-haiku"

        ai_cloud = self._mock_cloud_ai()
        ai_local = self._mock_local_ai()

        resp = responder._respond_ai("hello", ai_cloud,
                                     "UNKNOWN", None, ai_local)
        assert resp.method == "ai_fallback"
        ai_local.generate.assert_not_called()

    @patch("responder.tts_generate")
    @patch("responder.cfg")
    def test_local_only_uses_rejected_response(self, mock_cfg, mock_tts, responder):
        mock_cfg.ai_backend = "local_only"
        mock_cfg.ai_model = "claude-haiku"
        mock_tts.speak.return_value = "/tmp/local.wav"

        ai_cloud = self._mock_cloud_ai()
        ai_local = MagicMock()
        ai_local.generate.side_effect = QualityCheckFailed(
            "hedge_phrase", "I'm not sure but maybe...")

        resp = responder._respond_ai("something hard", ai_cloud,
                                     "UNKNOWN", None, ai_local)
        assert resp.method == "ai_local_forced"
        ai_cloud.respond.assert_not_called()

    @patch("tts_generate.speak", return_value="/tmp/error.wav")
    @patch("responder.tts_generate")
    @patch("responder.cfg")
    def test_local_only_connection_error_returns_error(self, mock_cfg, mock_tts,
                                                       mock_tts_mod, responder):
        mock_cfg.ai_backend = "local_only"
        mock_cfg.ai_model = "claude-haiku"
        mock_tts.speak.return_value = "/tmp/error.wav"

        ai_cloud = self._mock_cloud_ai()
        ai_local = MagicMock()
        ai_local.generate.side_effect = requests.exceptions.ConnectionError("refused")

        resp = responder._respond_ai("hello", ai_cloud,
                                     "UNKNOWN", None, ai_local)
        assert resp.method == "error_fallback"
        ai_cloud.respond.assert_not_called()

    @patch("responder.cfg")
    def test_no_local_available_falls_to_cloud(self, mock_cfg, responder):
        mock_cfg.ai_backend = "hybrid"
        mock_cfg.ai_model = "claude-haiku"

        ai_cloud = self._mock_cloud_ai()

        resp = responder._respond_ai("hello", ai_cloud,
                                     "UNKNOWN", None, ai_local=None)
        assert resp.method == "ai_fallback"

    @patch("responder.tts_generate")
    @patch("responder.cfg")
    def test_routing_log_present_on_local_success(self, mock_cfg, mock_tts, responder):
        mock_cfg.ai_backend = "hybrid"
        mock_cfg.ai_routing = {"conversation": "local_first"}
        mock_cfg.ai_model = "claude-haiku"
        mock_tts.speak.return_value = "/tmp/local.wav"

        ai_local = self._mock_local_ai()
        resp = responder._respond_ai("hello bender", self._mock_cloud_ai(),
                                     "UNKNOWN", None, ai_local)
        assert resp.routing_log is not None
        assert resp.routing_log["scenario"] == "conversation"
        assert resp.routing_log["final_method"] == "ai_local"
        assert resp.routing_log["quality_check_passed"] is True
