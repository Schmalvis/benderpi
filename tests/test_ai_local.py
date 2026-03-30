import sys
from unittest.mock import MagicMock

# anthropic is not installed in the test environment; stub it before any import
sys.modules.setdefault("anthropic", MagicMock())

import pytest
import requests
from unittest.mock import patch
from ai_local import check_response_quality, QualityCheckFailed, LocalAIResponder


class TestCheckResponseQuality:
    def test_good_response(self):
        passed, reason = check_response_quality("Bite my shiny metal ass!")
        assert passed is True
        assert reason == ""

    def test_too_short(self):
        passed, reason = check_response_quality("Ok.")
        assert passed is False
        assert reason == "too_short"

    def test_empty(self):
        passed, reason = check_response_quality("")
        assert passed is False
        assert reason == "too_short"

    def test_whitespace_only(self):
        passed, reason = check_response_quality("   ")
        assert passed is False
        assert reason == "too_short"

    def test_hedge_i_dont_know(self):
        passed, reason = check_response_quality(
            "I don't know the answer to that question.")
        assert passed is False
        assert reason == "hedge_phrase"

    def test_hedge_as_an_ai(self):
        passed, reason = check_response_quality(
            "As an AI, I cannot help with that request.")
        assert passed is False
        assert reason == "hedge_phrase"

    def test_hedge_language_model(self):
        passed, reason = check_response_quality(
            "I'm just a language model and can't do that.")
        assert passed is False
        assert reason == "hedge_phrase"

    def test_hedge_case_insensitive(self):
        passed, reason = check_response_quality("I'M NOT SURE about that.")
        assert passed is False
        assert reason == "hedge_phrase"

    def test_long_good_response(self):
        passed, reason = check_response_quality(
            "Listen here, meatbag. I'm Bender, the greatest robot ever built. "
            "Now get me a beer.")
        assert passed is True
        assert reason == ""


class TestQualityCheckFailed:
    def test_carries_reason_and_text(self):
        exc = QualityCheckFailed("hedge_phrase", "I'm not sure about that")
        assert exc.reason == "hedge_phrase"
        assert exc.response_text == "I'm not sure about that"


class TestLocalAIResponder:
    def setup_method(self):
        self._hailo_patch = patch("ai_local._HailoLLMResponder._load", return_value=False)
        self._hailo_patch.start()

    def teardown_method(self):
        self._hailo_patch.stop()

    def _mock_ollama_response(self, content, status=200):
        mock_resp = MagicMock()
        mock_resp.status_code = status
        mock_resp.json.return_value = {"message": {"content": content}}
        mock_resp.raise_for_status = MagicMock()
        if status >= 400:
            mock_resp.raise_for_status.side_effect = requests.HTTPError()
        return mock_resp

    def test_successful_generation(self):
        responder = LocalAIResponder()
        with patch("ai_local.requests.post") as mock_post:
            mock_post.return_value = self._mock_ollama_response(
                "Bite my shiny metal ass, meatbag!")
            result = responder.generate("Hello Bender")
        assert result == "Bite my shiny metal ass, meatbag!"
        assert len(responder._ollama.history) == 2

    def test_quality_check_failure_raises(self):
        responder = LocalAIResponder()
        with patch("ai_local.requests.post") as mock_post:
            mock_post.return_value = self._mock_ollama_response("I don't know.")
            with pytest.raises(QualityCheckFailed) as exc_info:
                responder.generate("Who was the first king?")
        assert exc_info.value.reason == "hedge_phrase"
        assert exc_info.value.response_text == "I don't know."

    def test_too_short_raises(self):
        responder = LocalAIResponder()
        with patch("ai_local.requests.post") as mock_post:
            mock_post.return_value = self._mock_ollama_response("Ok.")
            with pytest.raises(QualityCheckFailed) as exc_info:
                responder.generate("Hi")
        assert exc_info.value.reason == "too_short"

    def test_timeout_raises(self):
        responder = LocalAIResponder()
        with patch("ai_local.requests.post") as mock_post:
            mock_post.side_effect = requests.exceptions.Timeout("timed out")
            with pytest.raises(requests.exceptions.Timeout):
                responder.generate("Tell me something")

    def test_connection_error_raises(self):
        responder = LocalAIResponder()
        with patch("ai_local.requests.post") as mock_post:
            mock_post.side_effect = requests.exceptions.ConnectionError("refused")
            with pytest.raises(requests.exceptions.ConnectionError):
                responder.generate("Hello")

    def test_history_management(self):
        responder = LocalAIResponder()
        with patch("ai_local.requests.post") as mock_post:
            mock_post.return_value = self._mock_ollama_response(
                "Yeah yeah, what do you want, meatbag?")
            for i in range(10):
                responder.generate(f"Message {i}")
        assert len(responder._ollama.history) <= 12

    def test_clear_history(self):
        responder = LocalAIResponder()
        with patch("ai_local.requests.post") as mock_post:
            mock_post.return_value = self._mock_ollama_response(
                "Whatever, meatbag. Leave me alone.")
            responder.generate("Hi")
        assert len(responder._ollama.history) == 2
        responder.clear_history()
        assert len(responder._ollama.history) == 0

    def test_sends_system_prompt(self):
        responder = LocalAIResponder()
        with patch("ai_local.requests.post") as mock_post:
            mock_post.return_value = self._mock_ollama_response(
                "I'm Bender, baby! The greatest robot!")
            responder.generate("Who are you?")
            call_kwargs = mock_post.call_args.kwargs
            messages = call_kwargs["json"]["messages"]
            assert messages[0]["role"] == "system"
            assert "Bender" in messages[0]["content"]
