"""Tests for RealClipHandler."""
import json
import os
import pytest
from handlers.clip_handler import RealClipHandler


@pytest.fixture
def clip_fixture(tmp_path):
    """Create a minimal index.json and dummy WAV files for testing."""
    # Create WAV files
    wav_dir = tmp_path / "speech" / "wav"
    wav_dir.mkdir(parents=True)
    (wav_dir / "hello.wav").write_bytes(b"RIFF")
    (wav_dir / "gotit.wav").write_bytes(b"RIFF")
    (wav_dir / "solongcoffinstuffers.wav").write_bytes(b"RIFF")
    (wav_dir / "hahohwaityoureseriousletmelaughevenharder.wav").write_bytes(b"RIFF")

    # Create index.json
    resp_dir = tmp_path / "speech" / "responses"
    resp_dir.mkdir(parents=True)
    index = {
        "greeting": ["speech/wav/hello.wav"],
        "affirmation": ["speech/wav/gotit.wav"],
        "dismissal": ["speech/wav/solongcoffinstuffers.wav"],
        "joke": ["speech/wav/hahohwaityoureseriousletmelaughevenharder.wav"],
    }
    index_path = resp_dir / "index.json"
    index_path.write_text(json.dumps(index))

    return RealClipHandler(index_path=str(index_path), base_dir=str(tmp_path))


def test_intents_list():
    assert set(RealClipHandler.intents) >= {"GREETING", "AFFIRMATION", "DISMISSAL", "JOKE"}


def test_handle_greeting_returns_real_clip(clip_fixture):
    handler = clip_fixture
    response = handler.handle("hello", "GREETING")
    assert response is not None
    assert response.method == "real_clip"
    assert response.intent == "GREETING"
    assert response.wav_path.endswith("hello.wav")


def test_handle_affirmation(clip_fixture):
    response = clip_fixture.handle("thanks", "AFFIRMATION")
    assert response is not None
    assert response.method == "real_clip"
    assert response.intent == "AFFIRMATION"


def test_handle_dismissal(clip_fixture):
    response = clip_fixture.handle("bye", "DISMISSAL")
    assert response is not None
    assert response.method == "real_clip"
    assert response.intent == "DISMISSAL"


def test_handle_joke(clip_fixture):
    response = clip_fixture.handle("tell me a joke", "JOKE")
    assert response is not None
    assert response.method == "real_clip"
    assert response.intent == "JOKE"


def test_handle_wrong_intent_returns_none(clip_fixture):
    """WEATHER is not handled by RealClipHandler — should return None."""
    response = clip_fixture.handle("weather", "WEATHER")
    assert response is None


def test_handle_no_clips_for_intent(tmp_path):
    """Returns None when no clips exist for the intent."""
    resp_dir = tmp_path / "speech" / "responses"
    resp_dir.mkdir(parents=True)
    index = {"greeting": []}  # empty list
    index_path = resp_dir / "index.json"
    index_path.write_text(json.dumps(index))
    handler = RealClipHandler(index_path=str(index_path), base_dir=str(tmp_path))
    assert handler.handle("hello", "GREETING") is None


def test_handle_missing_wav_returns_none(tmp_path):
    """Returns None when the WAV file referenced in the index doesn't exist on disk."""
    resp_dir = tmp_path / "speech" / "responses"
    resp_dir.mkdir(parents=True)
    index = {"greeting": ["speech/wav/nonexistent.wav"]}
    index_path = resp_dir / "index.json"
    index_path.write_text(json.dumps(index))
    handler = RealClipHandler(index_path=str(index_path), base_dir=str(tmp_path))
    assert handler.handle("hello", "GREETING") is None


def test_load_invalid_index_returns_empty(tmp_path):
    """Gracefully handles a corrupt index.json."""
    bad_index = tmp_path / "bad_index.json"
    bad_index.write_text("not valid json{{{")
    handler = RealClipHandler(index_path=str(bad_index), base_dir=str(tmp_path))
    assert handler._index == {}


def test_sub_key_passed_through(clip_fixture):
    response = clip_fixture.handle("hello", "GREETING", sub_key="morning")
    assert response is not None
    assert response.sub_key == "morning"


def test_handle_object_entries(tmp_path):
    """RealClipHandler works when index.json entries are {file, label} dicts."""
    wav_dir = tmp_path / "speech" / "wav"
    wav_dir.mkdir(parents=True)
    (wav_dir / "hello.wav").write_bytes(b"RIFF")

    resp_dir = tmp_path / "speech" / "responses"
    resp_dir.mkdir(parents=True)
    index = {
        "greeting": [{"file": "speech/wav/hello.wav", "label": "Hey there meatbag"}],
    }
    index_path = resp_dir / "index.json"
    index_path.write_text(json.dumps(index))

    handler = RealClipHandler(index_path=str(index_path), base_dir=str(tmp_path))
    response = handler.handle("hello", "GREETING")
    assert response is not None
    assert response.method == "real_clip"
    assert response.wav_path.endswith("hello.wav")
