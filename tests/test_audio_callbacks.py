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


class TestAbort:
    def test_abort_stops_playback_early(self, audio_mod):
        """Calling abort() during play() should stop playback before all chunks."""
        wav = _make_wav(num_frames=44100 * 2)  # 2 seconds
        try:
            chunks_played = []
            def _on_chunk(v):
                chunks_played.append(v)
                if len(chunks_played) == 3:
                    audio_mod.abort()
            audio_mod.play(wav, on_chunk=_on_chunk)
            assert len(chunks_played) < 20
            assert audio_mod.was_aborted() is True
        finally:
            os.unlink(wav)

    def test_was_aborted_false_on_normal_play(self, audio_mod):
        wav = _make_wav()
        try:
            audio_mod.play(wav)
            assert audio_mod.was_aborted() is False
        finally:
            os.unlink(wav)

    def test_on_done_called_even_on_abort(self, audio_mod):
        wav = _make_wav(num_frames=44100 * 2)
        try:
            done_calls = []
            audio_mod.play(wav, on_chunk=lambda v: audio_mod.abort(),
                          on_done=lambda: done_calls.append(1))
            assert done_calls == [1]
        finally:
            os.unlink(wav)

    def test_abort_clears_on_next_play(self, audio_mod):
        wav = _make_wav(num_frames=44100 * 2)
        try:
            audio_mod.play(wav, on_chunk=lambda v: audio_mod.abort())
            assert audio_mod.was_aborted() is True
            chunks = []
            audio_mod.play(wav, on_chunk=chunks.append)
            assert audio_mod.was_aborted() is False
            assert len(chunks) > 10
        finally:
            os.unlink(wav)

    def test_abort_on_play_oneshot(self, audio_mod):
        wav = _make_wav(num_frames=44100 * 2)
        try:
            chunks = []
            def _on_chunk(v):
                chunks.append(v)
                if len(chunks) == 3:
                    audio_mod.abort()
            audio_mod.play_oneshot(wav, on_chunk=_on_chunk)
            assert len(chunks) < 20
            assert audio_mod.was_aborted() is True
        finally:
            os.unlink(wav)


# ---------------------------------------------------------------------------
# play_stream_oneshot tests
# ---------------------------------------------------------------------------

def _make_wav_bytes_stream(n_frames: int = 512) -> bytes:
    """Return minimal WAV file bytes (44100Hz mono int16)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(44100)
        wf.writeframes(b"\x00" * n_frames * 2)
    return buf.getvalue()


def _write_wav_stream(path: str, n_frames: int = 512):
    with open(path, "wb") as f:
        f.write(_make_wav_bytes_stream(n_frames))


def test_play_stream_oneshot_plays_all_clips_and_unlinks(tmp_path):
    """play_stream_oneshot plays each WAV and unlinks it."""
    import audio

    paths = []
    for i in range(3):
        p = str(tmp_path / f"clip{i}.wav")
        _write_wav_stream(p)
        paths.append(p)

    mock_stream = MagicMock()
    mock_stream.is_active.return_value = False
    mock_pa = MagicMock()
    mock_pa.open.return_value = mock_stream

    with patch.object(audio, "_pa", mock_pa), \
         patch.object(audio, "_stream", None):
        audio.play_stream_oneshot(iter(paths))

    for p in paths:
        assert not os.path.exists(p), f"WAV not unlinked: {p}"

    mock_pa.open.assert_called_once()
    mock_stream.stop_stream.assert_called_once()
    mock_stream.close.assert_called_once()


def test_play_stream_oneshot_calls_on_done_once(tmp_path):
    """on_done is called exactly once after all clips play."""
    import audio

    paths = [str(tmp_path / f"c{i}.wav") for i in range(2)]
    for p in paths:
        _write_wav_stream(p)

    done_calls = []
    mock_stream = MagicMock()
    mock_stream.is_active.return_value = False
    mock_pa = MagicMock()
    mock_pa.open.return_value = mock_stream

    with patch.object(audio, "_pa", mock_pa), \
         patch.object(audio, "_stream", None):
        audio.play_stream_oneshot(iter(paths), on_done=lambda: done_calls.append(1))

    assert done_calls == [1], f"on_done called {len(done_calls)} times, expected 1"


def test_play_stream_oneshot_empty_iterator(tmp_path):
    """Empty iterator: completes cleanly, on_done still called."""
    import audio

    done = []
    mock_stream = MagicMock()
    mock_stream.is_active.return_value = False
    mock_pa = MagicMock()
    mock_pa.open.return_value = mock_stream

    with patch.object(audio, "_pa", mock_pa), \
         patch.object(audio, "_stream", None):
        audio.play_stream_oneshot(iter([]), on_done=lambda: done.append(1))

    assert done == [1]
