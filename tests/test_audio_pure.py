"""Unit tests for audio.py's pure RMS helpers — rms() and rms_to_ratio().

These are the amplitude functions behind LED sync; deterministic and
mockable, no hardware required. rms()/rms_to_ratio() are exactly the pure
math behind the 6-day silent-mic incident diagnosis, so they're worth
locking down independently of the hardware-adjacent MicReader tests.
"""
import struct
import sys
import types

import pytest

# Fake pyaudio so `import audio` succeeds without touching hardware —
# same pattern used by test_mic_reader.py / test_audio_callbacks.py.
_fake_pyaudio = types.SimpleNamespace(
    paInt16=8,
    PyAudio=lambda: types.SimpleNamespace(),
)
sys.modules.setdefault("pyaudio", _fake_pyaudio)

sys.path.insert(0, "scripts")

import audio  # noqa: E402
from audio import rms, rms_to_ratio, RMS_FLOOR, RMS_CEILING  # noqa: E402


def _pcm16(*values: int) -> bytes:
    return struct.pack(f"<{len(values)}h", *values)


class TestRms:
    def test_silence_is_zero(self):
        assert rms(_pcm16(0, 0, 0, 0), sample_width=2) == 0.0

    def test_zero_length_input_is_zero(self):
        assert rms(b"", sample_width=2) == 0.0

    def test_constant_amplitude_16bit(self):
        # RMS of a constant-magnitude signal equals its magnitude.
        assert rms(_pcm16(1000, -1000, 1000, -1000), sample_width=2) == pytest.approx(1000.0)

    def test_max_amplitude_16bit(self):
        assert rms(_pcm16(32767, -32768), sample_width=2) == pytest.approx(32767.5, rel=1e-3)

    def test_sine_like_signal_16bit(self):
        # Known RMS for a symmetric square-ish signal: sqrt(mean(x^2)).
        data = _pcm16(2000, -2000, 4000, -4000)
        expected = (2000 ** 2 * 2 + 4000 ** 2 * 2) / 4
        expected = expected ** 0.5
        assert rms(data, sample_width=2) == pytest.approx(expected)

    def test_8bit_sample_width(self):
        data = struct.pack("<4b", 50, -50, 50, -50)
        assert rms(data, sample_width=1) == pytest.approx(50.0)


class TestRmsToRatio:
    def test_below_floor_clamps_to_zero(self):
        assert rms_to_ratio(0.0) == 0.0
        assert rms_to_ratio(RMS_FLOOR - 1) == 0.0

    def test_at_floor_is_zero(self):
        assert rms_to_ratio(RMS_FLOOR) == 0.0

    def test_at_ceiling_is_one(self):
        assert rms_to_ratio(RMS_CEILING) == pytest.approx(1.0)

    def test_above_ceiling_clamps_to_one(self):
        assert rms_to_ratio(RMS_CEILING + 5000) == 1.0

    def test_midpoint_is_half(self):
        midpoint = RMS_FLOOR + (RMS_CEILING - RMS_FLOOR) / 2
        assert rms_to_ratio(midpoint) == pytest.approx(0.5)
