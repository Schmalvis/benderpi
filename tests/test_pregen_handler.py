"""Tests for PreGenHandler."""
import json
import pytest
from handlers.pregen_handler import PreGenHandler


@pytest.fixture
def pregen_fixture(tmp_path):
    """Create a minimal index.json with string-valued personal entries and dummy WAV files."""
    wav_dir = tmp_path / "speech" / "responses"
    wav_dir.mkdir(parents=True)
    (wav_dir / "job_1.wav").write_bytes(b"RIFF")
    (wav_dir / "age_1.wav").write_bytes(b"RIFF")

    index = {
        "personal": {
            "job": "speech/responses/job_1.wav",
            "age": "speech/responses/age_1.wav",
        }
    }
    index_path = tmp_path / "speech" / "responses" / "index.json"
    index_path.write_text(json.dumps(index))

    return PreGenHandler(index_path=str(index_path), base_dir=str(tmp_path))


def test_intents_list():
    assert "PERSONAL" in PreGenHandler.intents


def test_handle_valid_sub_key(pregen_fixture):
    response = pregen_fixture.handle("what is your job", "PERSONAL", sub_key="job")
    assert response is not None
    assert response.method == "pre_gen_tts"
    assert response.intent == "PERSONAL"
    assert response.sub_key == "job"
    assert response.wav_path.endswith("job_1.wav")


def test_handle_age_sub_key(pregen_fixture):
    response = pregen_fixture.handle("how old are you", "PERSONAL", sub_key="age")
    assert response is not None
    assert response.method == "pre_gen_tts"
    assert response.wav_path.endswith("age_1.wav")


def test_handle_missing_sub_key_returns_none(pregen_fixture):
    """Returns None when sub_key is not in the personal index."""
    response = pregen_fixture.handle("how do you feel", "PERSONAL", sub_key="feelings")
    assert response is None


def test_handle_no_sub_key_returns_none(pregen_fixture):
    """Returns None when sub_key is None."""
    response = pregen_fixture.handle("something personal", "PERSONAL", sub_key=None)
    assert response is None


def test_intents_does_not_include_greeting():
    """GREETING is not in PreGenHandler.intents — dispatcher won't route to it."""
    assert "GREETING" not in PreGenHandler.intents
    assert "WEATHER" not in PreGenHandler.intents


def test_handle_missing_wav_returns_none(tmp_path):
    """Returns None when the WAV file referenced in the index doesn't exist on disk."""
    resp_dir = tmp_path / "speech" / "responses"
    resp_dir.mkdir(parents=True)
    index = {"personal": {"job": "speech/responses/nonexistent.wav"}}
    index_path = resp_dir / "index.json"
    index_path.write_text(json.dumps(index))
    handler = PreGenHandler(index_path=str(index_path), base_dir=str(tmp_path))
    assert handler.handle("what is your job", "PERSONAL", sub_key="job") is None


def test_load_invalid_index_returns_empty(tmp_path):
    """Gracefully handles a corrupt index.json."""
    bad_index = tmp_path / "bad_index.json"
    bad_index.write_text("not valid json{{{")
    handler = PreGenHandler(index_path=str(bad_index), base_dir=str(tmp_path))
    assert handler._index == {}


def test_handles_list_value_in_personal(tmp_path):
    """Supports list values in personal (defensive — real index uses strings)."""
    resp_dir = tmp_path / "speech" / "responses"
    resp_dir.mkdir(parents=True)
    (resp_dir / "job_1.wav").write_bytes(b"RIFF")
    index = {"personal": {"job": ["speech/responses/job_1.wav"]}}
    index_path = resp_dir / "index.json"
    index_path.write_text(json.dumps(index))
    handler = PreGenHandler(index_path=str(index_path), base_dir=str(tmp_path))
    response = handler.handle("what is your job", "PERSONAL", sub_key="job")
    assert response is not None
    assert response.method == "pre_gen_tts"
