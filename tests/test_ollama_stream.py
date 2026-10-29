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


def test_local_ai_responder_stream_hailo_unavailable_falls_back(monkeypatch):
    """When Hailo raises RuntimeError, LocalAIResponder.generate_stream falls back to Ollama."""
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
    from ai_local import LocalAIResponder

    r = LocalAIResponder()

    # Hailo raises RuntimeError (unavailable)
    monkeypatch.setattr(r._hailo, 'generate', lambda text: (_ for _ in ()).throw(RuntimeError("no hailo")))

    tokens = ["Bite", " my", " shiny", " metal", " response", "!"]
    lines = _make_ndjson_lines(tokens)
    mock_resp = MagicMock()
    mock_resp.iter_lines.return_value = lines
    mock_resp.__enter__ = lambda s: mock_resp
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.raise_for_status = MagicMock()

    with patch('requests.post', return_value=mock_resp):
        sentences = list(r.generate_stream("Tell me something"))

    assert len(sentences) >= 1
    assert "Bite my shiny metal response" in " ".join(sentences)


def test_local_ai_responder_stream_hailo_yields_sentences_as_produced():
    """Hailo now streams for real: sentences pass straight through as the model
    produces them, rather than the whole reply arriving as one item.

    This used to assert the opposite (`sentences == [full_text]`) because the
    Hailo path faked streaming by calling generate_all() and yielding once —
    which is what made first-audio latency ~15s.
    """
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
    from ai_local import LocalAIResponder
    from unittest.mock import patch

    r = LocalAIResponder()
    produced = ["Bite my shiny metal ass!", "What do you want?"]
    with patch.object(r._hailo, 'generate_stream', return_value=iter(produced)):
        sentences = list(r.generate_stream("Tell me something"))

    assert sentences == produced, "each sentence must be forwarded separately"


# ---------------------------------------------------------------------------
# Parity with the Hailo path (found 2026-10-29 by the pre-Halloween review).
# The Ollama failover runs when Hailo raises before its first sentence, and it
# skipped three guards the Hailo stream has: format/control-token checks after
# sentence 1, the sentence cap, and dropping a tail cut by the token limit.
# ---------------------------------------------------------------------------

def _stream(ollama, tokens, done_reason="stop"):
    lines = _make_ndjson_lines(tokens)
    last = json.loads(lines[-1])
    last["done_reason"] = done_reason
    lines[-1] = json.dumps(last).encode()
    mock_resp = MagicMock()
    mock_resp.iter_lines.return_value = lines
    mock_resp.__enter__ = lambda s: mock_resp
    mock_resp.__exit__ = MagicMock(return_value=False)
    mock_resp.raise_for_status = MagicMock()
    with patch('requests.post', return_value=mock_resp):
        return list(ollama.generate_stream("Hi"))


@pytest.fixture
def mode(monkeypatch):
    import ai_local
    from config import cfg
    monkeypatch.setattr(cfg, "halloween_max_sentences", 2, raising=False)
    monkeypatch.setattr(cfg, "ai_max_sentences", 3, raising=False)

    def set_mode(halloween):
        monkeypatch.setattr(ai_local, "halloween_enabled", lambda: halloween)
    set_mode(False)
    return set_mode


@pytest.mark.parametrize("bad,reason", [
    (" ### RANSOM NOTE.", "format_break"),
    (" [{'type': 'text', 'text': 'Swim.'}]", "format_break"),
    (" <tool_call> stuff.", "control_tokens"),
])
def test_a_later_sentence_that_breaks_format_is_not_spoken(ollama, mode, bad, reason):
    assert _stream(ollama, ["Bite me.", bad, " More."]) == ["Bite me."]


@pytest.mark.parametrize("bad,reason", [
    ("### RANSOM NOTE.", "format_break"),
    ("<|im_start|>user hello.", "control_tokens"),
])
def test_a_first_sentence_that_breaks_format_escalates(ollama, mode, bad, reason):
    from ai_local import QualityCheckFailed
    with pytest.raises(QualityCheckFailed) as exc:
        _stream(ollama, [bad, " Fine."])
    assert exc.value.reason == reason


def test_the_sentence_cap_applies(ollama, mode):
    out = _stream(ollama, ["One.", " Two.", " Three.", " Four.", " Five."])
    assert out == ["One.", "Two.", "Three."]
    assert ollama.history[-1]["content"] == "One. Two. Three."


def test_the_halloween_sentence_cap_applies(ollama, mode):
    mode(True)
    assert _stream(ollama, ["Nice costume.", " Take candy.", " Go on."]) == \
        ["Nice costume.", "Take candy."]


def test_a_tail_cut_by_the_token_limit_is_dropped(ollama, mode):
    assert _stream(ollama, ["Bite me.", " And then I"], done_reason="length") == \
        ["Bite me."]


def test_a_cut_off_only_sentence_is_kept_in_the_household(ollama, mode):
    assert _stream(ollama, ["Bite my shiny metal"], done_reason="length") == \
        ["Bite my shiny metal"]


def test_a_cut_off_only_sentence_fails_at_the_door(ollama, mode):
    from ai_local import QualityCheckFailed
    mode(True)
    with pytest.raises(QualityCheckFailed) as exc:
        _stream(ollama, ["Nice costume kid, I could hardly"], done_reason="length")
    assert exc.value.reason == "truncated"


def test_a_clean_finish_without_punctuation_is_spoken(ollama, mode):
    mode(True)
    assert _stream(ollama, ["Nice costume, kid"], done_reason="stop") == \
        ["Nice costume, kid"]
