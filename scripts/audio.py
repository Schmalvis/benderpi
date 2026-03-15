#!/usr/bin/env python3
"""
audio.py — WAV playback with LED amplitude visualisation.

Keeps a single PyAudio output stream open at all times to prevent
WM8960/I2S DAC click/pop between clips. A background keepalive thread
writes silence continuously so the card never goes idle.

play() acquires a lock, so keepalive yields cleanly during playback.
"""

import threading
import time
import wave

import numpy as np
import pyaudio

import leds

CHUNK        = 512      # samples per buffer (~11.6ms at 44100Hz)
SAMPLE_RATE  = 44100
CHANNELS     = 1
FORMAT       = pyaudio.paInt16
RMS_FLOOR    = 200
RMS_CEILING  = 8000

SILENCE_PRE  = 0.05    # 50ms silence before speech (DAC warm-up)
SILENCE_POST = 0.20    # 200ms silence after speech (DAC settle)

_KEEPALIVE_INTERVAL = CHUNK / SAMPLE_RATE  # ~11.6ms between keepalive writes

# ---------------------------------------------------------------------------
# Persistent stream
# ---------------------------------------------------------------------------

_pa     = None
_stream = None
_lock   = threading.Lock()
_SILENCE_CHUNK = b'\x00' * (CHUNK * 2 * CHANNELS)


def _ensure_stream() -> pyaudio.Stream:
    """Open or re-open the output stream if needed. Call with _lock held."""
    global _pa, _stream
    if _stream is not None:
        try:
            if _stream.is_active():
                return _stream
        except Exception:
            pass
    if _pa is not None:
        try:
            _pa.terminate()
        except Exception:
            pass
    _pa = pyaudio.PyAudio()
    _stream = _pa.open(
        format=FORMAT,
        channels=CHANNELS,
        rate=SAMPLE_RATE,
        output=True,
        frames_per_buffer=CHUNK,
    )
    return _stream


def _silence(duration_s: float) -> bytes:
    n = int(SAMPLE_RATE * duration_s)
    return b'\x00' * (n * 2 * CHANNELS)


def _keepalive_worker():
    """Background thread: write silence to keep card from going idle."""
    while True:
        time.sleep(_KEEPALIVE_INTERVAL)
        with _lock:
            try:
                _ensure_stream().write(_SILENCE_CHUNK)
            except Exception:
                pass  # _ensure_stream will reopen on next call


def start_keepalive():
    """Start keepalive thread. Call once at startup."""
    t = threading.Thread(target=_keepalive_worker, daemon=True)
    t.start()


# ---------------------------------------------------------------------------
# RMS helpers for LED sync
# ---------------------------------------------------------------------------

def rms(data: bytes, sample_width: int) -> float:
    dtype = np.int16 if sample_width == 2 else np.int8
    samples = np.frombuffer(data, dtype=dtype).astype(np.float32)
    return float(np.sqrt(np.mean(samples ** 2))) if len(samples) else 0.0


def rms_to_ratio(value: float) -> float:
    clamped = max(0.0, value - RMS_FLOOR)
    return min(clamped / (RMS_CEILING - RMS_FLOOR), 1.0)


# ---------------------------------------------------------------------------
# Playback
# ---------------------------------------------------------------------------

def play(wav_path: str):
    """
    Play a WAV file, driving LEDs in sync.

    Acquires the global lock, so the keepalive yields during playback.
    Stream is never closed — no DAC pop between clips.
    """
    with _lock:
        stream = _ensure_stream()

        # Pre-silence: let DAC settle before speech hits
        stream.write(_silence(SILENCE_PRE))

        with wave.open(wav_path, 'rb') as wf:
            sw = wf.getsampwidth()
            data = wf.readframes(CHUNK)
            while data:
                stream.write(data)
                leds.set_level(rms_to_ratio(rms(data, sw)))
                data = wf.readframes(CHUNK)

        # Post-silence: let last samples drain before card goes quiet
        stream.write(_silence(SILENCE_POST))

    leds.all_off()
