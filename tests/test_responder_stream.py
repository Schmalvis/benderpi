"""Tests for responder._respond_ai() streaming path."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

import pytest
from unittest.mock import MagicMock, patch
from handler_base import ResponseStream, Response


def _make_responder():
    from responder import Responder
    r = Responder()
    return r


def _make_ai_local(sentences):
    """Return a mock LocalAIResponder whose generate_stream yields given sentences."""
    mock = MagicMock()
    mock.generate_stream.return_value = iter(sentences)
    return mock


def _make_ai_cloud():
    return MagicMock()


def test_respond_ai_local_first_returns_response_stream(monkeypatch):
    monkeypatch.setattr('config.cfg.ai_backend', 'hybrid')
    monkeypatch.setattr('config.cfg.ai_routing', {'conversation': 'local_first', 'knowledge': 'local_first', 'creative': 'local_first'})
    monkeypatch.setattr('config.cfg.local_llm_model', 'test-model')

    r = _make_responder()
    ai_local = _make_ai_local(["Bite my shiny metal response!", "How about that."])
    ai_cloud = _make_ai_cloud()

    result = r._respond_ai("what is 2+2", ai_cloud, "UNKNOWN", None, ai_local)

    assert isinstance(result, ResponseStream)
    assert result.method == "ai_local_stream"
    sentences = list(result.sentence_iter)
    assert "Bite my shiny metal response!" in sentences


def test_respond_ai_local_first_escalates_on_quality_fail(monkeypatch):
    monkeypatch.setattr('config.cfg.ai_backend', 'hybrid')
    monkeypatch.setattr('config.cfg.ai_routing', {'conversation': 'local_first'})

    from ai_local import QualityCheckFailed

    r = _make_responder()
    ai_local = MagicMock()
    ai_local.generate_stream.side_effect = QualityCheckFailed("hedge_phrase", "I'm not sure.")
    ai_cloud = _make_ai_cloud()

    # Cloud path returns a ResponseStream
    from handler_base import ResponseStream as RS
    ai_cloud.respond_streaming.return_value = iter(["Cloud response."])

    result = r._respond_ai("what is 2+2", ai_cloud, "UNKNOWN", None, ai_local)

    # Should have escalated to cloud
    assert isinstance(result, RS)
    assert result.method == "ai_streaming"


def test_respond_ai_local_only_uses_response_even_on_quality_fail(monkeypatch):
    monkeypatch.setattr('config.cfg.ai_backend', 'local_only')
    monkeypatch.setattr('config.cfg.local_llm_model', 'test-model')

    from ai_local import QualityCheckFailed

    r = _make_responder()
    ai_local = MagicMock()
    ai_local.generate_stream.side_effect = QualityCheckFailed("hedge_phrase", "I'm not sure about that.")
    ai_cloud = _make_ai_cloud()

    result = r._respond_ai("what is 2+2", ai_cloud, "UNKNOWN", None, ai_local)

    # local_only — should use failed response rather than escalating
    assert isinstance(result, Response)
    assert result.method == "ai_local_forced"
    assert "not sure" in result.text
