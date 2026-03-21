"""Tests for response priority chain."""
import json
from unittest.mock import MagicMock


def _make_index(tmp_path):
    """Create a minimal test index.json."""
    index = {
        "greeting": ["speech/wav/hello.wav"],
        "dismissal": ["speech/wav/bye.wav"],
        "joke": ["speech/wav/joke.wav"],
        "affirmation": ["speech/wav/gotit.wav"],
        "personal": {"age": "speech/responses/personal/age.wav"},
        "ha_confirm": [],
        "promoted": [],
    }
    idx_path = tmp_path / "index.json"
    idx_path.write_text(json.dumps(index))
    return str(idx_path)


def test_response_dataclass():
    from responder import Response
    r = Response(
        text="hello", wav_path="/tmp/test.wav", method="real_clip",
        intent="GREETING", sub_key=None, is_temp=False,
        needs_thinking=False, model=None,
    )
    assert r.intent == "GREETING"
    assert r.is_temp is False
    assert r.needs_thinking is False


def test_greeting_returns_real_clip(tmp_path):
    from responder import Responder
    idx = _make_index(tmp_path)
    wav_path = tmp_path / "speech" / "wav"
    wav_path.mkdir(parents=True)
    (wav_path / "hello.wav").write_bytes(b"RIFF")
    r = Responder(index_path=idx, base_dir=str(tmp_path))
    resp = r.get_response("hello", ai=None)
    assert resp.intent == "GREETING"
    assert resp.method == "real_clip"
    assert resp.is_temp is False


def test_unknown_falls_to_ai(tmp_path):
    from responder import Responder
    idx = _make_index(tmp_path)
    mock_ai = MagicMock()
    mock_ai.respond.return_value = "/tmp/ai_response.wav"
    mock_ai.history = [{"role": "assistant", "content": "test reply"}]
    r = Responder(index_path=idx, base_dir=str(tmp_path))
    resp = r.get_response("what is quantum computing", ai=mock_ai)
    assert resp.intent == "UNKNOWN"
    assert resp.method == "ai_fallback"
    assert resp.is_temp is True
    assert resp.needs_thinking is True
    mock_ai.respond.assert_called_once()


def test_dismissal_intent(tmp_path):
    from responder import Responder
    idx = _make_index(tmp_path)
    wav_path = tmp_path / "speech" / "wav"
    wav_path.mkdir(parents=True)
    (wav_path / "bye.wav").write_bytes(b"RIFF")
    r = Responder(index_path=idx, base_dir=str(tmp_path))
    resp = r.get_response("goodbye", ai=None)
    assert resp.intent == "DISMISSAL"


def test_personal_returns_pre_gen_tts(tmp_path):
    from responder import Responder
    idx = _make_index(tmp_path)
    resp_path = tmp_path / "speech" / "responses" / "personal"
    resp_path.mkdir(parents=True)
    (resp_path / "age.wav").write_bytes(b"RIFF")
    r = Responder(index_path=idx, base_dir=str(tmp_path))
    resp = r.get_response("how old are you", ai=None)
    assert resp.intent == "PERSONAL"
    assert resp.sub_key == "age"
    assert resp.method == "pre_gen_tts"
    assert resp.is_temp is False


# ------------------------------------------------------------------
# Dispatch table tests
# ------------------------------------------------------------------

class TestDispatchTable:
    def test_all_intents_registered(self, tmp_path):
        from responder import Responder
        idx = _make_index(tmp_path)
        r = Responder(index_path=idx, base_dir=str(tmp_path))
        expected = ["GREETING", "AFFIRMATION", "DISMISSAL", "JOKE",
                    "PERSONAL", "PROMOTED", "WEATHER", "NEWS",
                    "HA_CONTROL", "TIMER", "TIMER_CANCEL", "TIMER_STATUS"]
        for intent in expected:
            assert intent in r._dispatch, f"Missing handler for {intent}"

    def test_unknown_intent_not_in_dispatch(self, tmp_path):
        from responder import Responder
        idx = _make_index(tmp_path)
        r = Responder(index_path=idx, base_dir=str(tmp_path))
        assert "UNKNOWN" not in r._dispatch
