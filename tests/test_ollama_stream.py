"""Tests for _OllamaResponder.generate_stream()."""
import json
import pytest
from unittest.mock import patch, MagicMock


def _make_ndjson_lines(tokens: list[str]) -> list[bytes]:
    """Build fake Ollama NDJSON streaming response."""
    lines = []
    for i, tok in enumerate(tokens):
        done = i == len(tokens) - 1
        lines.append(json.dumps({
            "message": {"role": "assistant", "content": tok},
            "done": done,
        }).encode())
    return lines


@pytest.fixture
def ollama():
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
    from ai_local import _OllamaResponder
    r = _OllamaResponder()
    return r


def test_generate_stream_yields_sentences(ollama):
    tokens = ["Hello", " there", "!", " How", " are", " you", "?"]
    lines = _make_ndjson_lines(tokens)

    mock_resp = MagicMock()
    mock_resp.iter_lines.return_value = lines
    mock_resp.__enter__ = lambda s: mock_resp
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.raise_for_status = MagicMock()

    with patch('requests.post', return_value=mock_resp):
        sentences = list(ollama.generate_stream("Hi"))

    assert len(sentences) >= 1
    full = " ".join(sentences)
    assert "Hello there" in full
    assert "How are you" in full


def test_generate_stream_quality_check_fails_on_hedge(ollama):
    tokens = ["I", "'m", " not", " sure", " about", " that", "."]
    lines = _make_ndjson_lines(tokens)

    mock_resp = MagicMock()
    mock_resp.iter_lines.return_value = lines
    mock_resp.__enter__ = lambda s: mock_resp
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.raise_for_status = MagicMock()

    from ai_local import QualityCheckFailed
    with patch('requests.post', return_value=mock_resp):
        with pytest.raises(QualityCheckFailed) as exc_info:
            list(ollama.generate_stream("What is the meaning of life?"))
    assert exc_info.value.reason == "hedge_phrase"


def test_generate_stream_appends_history_on_success(ollama):
    tokens = ["Great", " question", "!"]
    lines = _make_ndjson_lines(tokens)

    mock_resp = MagicMock()
    mock_resp.iter_lines.return_value = lines
    mock_resp.__enter__ = lambda s: mock_resp
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.raise_for_status = MagicMock()

    with patch('requests.post', return_value=mock_resp):
        list(ollama.generate_stream("Hi"))

    assert len(ollama.history) == 2  # user + assistant
    assert ollama.history[-1]["role"] == "assistant"


def test_generate_stream_rolls_back_history_on_quality_fail(ollama):
    tokens = ["As", " an", " AI", " I", " cannot", "."]
    lines = _make_ndjson_lines(tokens)

    mock_resp = MagicMock()
    mock_resp.iter_lines.return_value = lines
    mock_resp.__enter__ = lambda s: mock_resp
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.raise_for_status = MagicMock()

    from ai_local import QualityCheckFailed
    with patch('requests.post', return_value=mock_resp):
        with pytest.raises(QualityCheckFailed):
            list(ollama.generate_stream("Help me"))

    assert len(ollama.history) == 0  # rolled back
