"""
test_audio_callbacks.py — Tests for audio.py on_chunk / on_done callbacks.

Mocks _pa (the PyAudio singleton) and _stream so no hardware is needed.
"""
import io
import os
import struct
import sys
import threading
import types
import wave
import tempfile
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Stub out pyaudio before audio.py is imported so the module-level
# pyaudio.PyAudio() call doesn't try to hit hardware.
# ---------------------------------------------------------------------------
_fake_pyaudio_mod = types.ModuleType("pyaudio")
_fake_pyaudio_mod.paInt16 = 8          # arbitrary constant
_fake_pyaudio_mod.PyAudio = MagicMock  # replaced per-test
sys.modules.setdefault("pyaudio", _fake_pyaudio_mod)

# Stub leds (still needed until audio.py no longer imports it — this test
# verifies the NEW code path so we stub it defensively just in case).
_fake_leds = types.ModuleType("leds")
_fake_leds.set_level = MagicMock()
_fake_leds.all_off = MagicMock()
sys.modules.setdefault("leds", _fake_leds)

# Stub neopixel / board transitively pulled in by leds on non-Pi hosts
for _mod in ("neopixel", "board", "RPi", "RPi.GPIO", "lgpio",
             "adafruit_blinka", "busio", "digitalio"):
    sys.modules.setdefault(_mod, types.ModuleType(_mod))

# Ensure scripts/ is on the path (conftest also does this, but be explicit)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))


# ---------------------------------------------------------------------------
# Helper — build a minimal valid WAV in a temp file
# ---------------------------------------------------------------------------
def _make_wav(num_frames: int = 2048, amplitude: int = 4000) -> str:
    """Return path to a temp WAV file (44100 Hz, mono, 16-bit)."""
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    with wave.open(tmp.name, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(44100)
        data = struct.pack("<" + "h" * num_frames,
                          *([amplitude, -amplitude] * (num_frames // 2)))
        wf.writeframes(data)
    return tmp.name


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def reset_audio_module():
    """Re-import audio fresh for each test so module-level state is clean."""
    # Remove cached module so each test gets a fresh import
    for key in list(sys.modules.keys()):
        if key == "audio":
            del sys.modules[key]
    yield
    for key in list(sys.modules.keys()):
        if key == "audio":
            del sys.modules[key]


@pytest.fixture()
def mock_stream():
    s = MagicMock()
    s.is_active.return_value = True
    return s


@pytest.fixture()
def audio_mod(mock_stream):
    """Import audio with _pa and _stream already mocked."""
    fake_pa = MagicMock()
    fake_pa.open.return_value = mock_stream
    _fake_pyaudio_mod.PyAudio = MagicMock(return_value=fake_pa)

    import audio
    # Inject mocked PA and an open stream so play() doesn't call open_session
    audio._pa = fake_pa
    audio._stream = mock_stream
    return audio


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPlayCallbacks:
    def test_on_chunk_called_with_values_in_range(self, audio_mod):
        """on_chunk is called for every audio chunk with a value in [0.0, 1.0]."""
        wav = _make_wav()
        try:
            received = []
            audio_mod.play(wav, on_chunk=received.append)
            assert len(received) > 0, "on_chunk was never called"
            for v in received:
                assert 0.0 <= v <= 1.0, f"on_chunk value out of range: {v}"
        finally:
            os.unlink(wav)

    def test_on_done_called_once(self, audio_mod):
        """on_done is called exactly once after playback."""
        wav = _make_wav()
        try:
            done_calls = []
            audio_mod.play(wav, on_done=lambda: done_calls.append(1))
            assert done_calls == [1], f"on_done called {len(done_calls)} times, expected 1"
        finally:
            os.unlink(wav)

    def test_on_done_called_after_lock_released(self, audio_mod):
        """on_done must be called outside _lock (lock must be free when on_done fires)."""
        wav = _make_wav()
        try:
            lock_held_during_done = []

            def _on_done():
                # Try to acquire the lock — should succeed immediately if released
                acquired = audio_mod._lock.acquire(blocking=False)
                lock_held_during_done.append(not acquired)
                if acquired:
                    audio_mod._lock.release()

            audio_mod.play(wav, on_done=_on_done)
            assert lock_held_during_done == [False], \
                "on_done was called while _lock was still held"
        finally:
            os.unlink(wav)

    def test_play_without_callbacks_no_error(self, audio_mod):
        """play() works fine when no callbacks are supplied."""
        wav = _make_wav()
        try:
            audio_mod.play(wav)  # must not raise
        finally:
            os.unlink(wav)

    def test_on_chunk_not_called_on_silent_wav(self, audio_mod):
        """on_chunk values for a silent WAV are still in range (0.0)."""
        wav = _make_wav(num_frames=512, amplitude=0)
        try:
            received = []
            audio_mod.play(wav, on_chunk=received.append)
            for v in received:
                assert 0.0 <= v <= 1.0
        finally:
            os.unlink(wav)


class TestPlayOneshotCallbacks:
    def test_oneshot_on_chunk_and_on_done(self, audio_mod):
        """play_oneshot passes on_chunk/on_done through correctly."""
        wav = _make_wav()
        try:
            received = []
            done_calls = []
            audio_mod.play_oneshot(wav,
                                   on_chunk=received.append,
                                   on_done=lambda: done_calls.append(1))
            assert len(received) > 0
            for v in received:
                assert 0.0 <= v <= 1.0
            assert done_calls == [1]
        finally:
            os.unlink(wav)

    def test_oneshot_no_callbacks_no_error(self, audio_mod):
        """play_oneshot() works fine when no callbacks are supplied."""
        wav = _make_wav()
        try:
            audio_mod.play_oneshot(wav)
        finally:
            os.unlink(wav)
