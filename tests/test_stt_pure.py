"""Unit tests for stt.py's pure functions — _filter_hallucination() and
_wav_to_array(). Deterministic, mockable, no hardware/model required.

These sit directly behind the STT quality-gate that filters Whisper
hallucinations (the 6-day silent-mic incident's blast radius), so they're
worth locking down independently of the Hailo/CPU backend plumbing.
"""
import struct
import sys
import types
import wave

import numpy as np
import pytest

sys.path.insert(0, "scripts")

# stt.py imports audio.py (module-level pyaudio.PyAudio()) and webrtcvad;
# fake pyaudio so import succeeds without touching hardware, same pattern
# used by test_mic_reader.py / test_audio_pure.py.
_fake_pyaudio = types.SimpleNamespace(
    paInt16=8,
    PyAudio=lambda: types.SimpleNamespace(),
)
sys.modules.setdefault("pyaudio", _fake_pyaudio)

import stt  # noqa: E402
from stt import _filter_hallucination, _wav_to_array  # noqa: E402


def _write_wav(path, samples, *, sample_rate=16000, channels=1, sample_width=2):
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack(f"<{len(samples)}h", *samples))


class TestFilterHallucination:
    def test_exact_match_is_filtered(self):
        assert _filter_hallucination("thank you") == ""

    def test_exact_match_case_insensitive(self):
        assert _filter_hallucination("THANK YOU") == ""

    def test_trailing_punctuation_stripped_before_match(self):
        assert _filter_hallucination("Thank you.") == ""
        assert _filter_hallucination("subscribe!") == ""

    def test_substring_is_not_filtered(self):
        # _filter_hallucination does an exact (post-strip) match against the
        # configured phrase list, not a substring/contains check.
        text = "thank you very much for watching this video"
        assert _filter_hallucination(text) == text

    def test_empty_text_passes_through(self):
        assert _filter_hallucination("") == ""

    def test_normal_speech_passes_through(self):
        text = "hello there bender, what's the weather like"
        assert _filter_hallucination(text) == text

    def test_repetitive_character_garbage_is_filtered(self):
        assert _filter_hallucination("aaaaaaaaaaaaaaaaaaaaa") == ""
        assert _filter_hallucination("ZZZZZZZZZZ") == ""

    def test_implausibly_long_text_is_filtered(self):
        assert _filter_hallucination("word " * 60) == ""

    def test_reasonable_length_text_not_filtered(self):
        text = "turn on the office lights please"
        assert _filter_hallucination(text) == text


class TestWavToArray:
    def test_normalises_int16_to_float_range(self, tmp_path):
        wav_path = tmp_path / "test.wav"
        _write_wav(wav_path, [0, 16384, -16384, 32767, -32768])
        arr = _wav_to_array(str(wav_path))
        assert arr.dtype == np.float32
        assert arr[0] == pytest.approx(0.0)
        assert arr[1] == pytest.approx(0.5, abs=1e-3)
        assert arr[2] == pytest.approx(-0.5, abs=1e-3)
        assert arr[3] == pytest.approx(1.0, abs=1e-3)
        assert arr[4] == pytest.approx(-1.0, abs=1e-3)

    def test_empty_wav_returns_empty_array(self, tmp_path):
        wav_path = tmp_path / "empty.wav"
        _write_wav(wav_path, [])
        arr = _wav_to_array(str(wav_path))
        assert len(arr) == 0

    def test_wrong_sample_rate_is_read_verbatim(self, tmp_path):
        # _wav_to_array does not resample or validate the header's sample
        # rate — it blindly reads PCM frames. A 16kHz-expected caller fed a
        # file at another rate gets the raw samples back unchanged, no error.
        wav_path = tmp_path / "wrong_rate.wav"
        _write_wav(wav_path, [1000, -1000], sample_rate=44100)
        arr = _wav_to_array(str(wav_path))
        assert len(arr) == 2
        assert arr[0] == pytest.approx(1000 / 32768.0)
