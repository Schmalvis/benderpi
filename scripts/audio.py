#!/usr/bin/env python3
"""
audio.py — WAV playback with LED amplitude visualisation.

The output stream is opened at session start and closed at session end.
This keeps the DAC warm between clips within a conversation (no click),
while freeing the audio device during wake-word listening so the mic
stream can operate without sample-rate conflicts on the WM8960.

API:
    open_session()   — open output stream (call after wake word detected)
    close_session()  — close output stream (call after session ends)
    play(wav_path)   — play a WAV file
"""

import os
import threading
import wave

import numpy as np
import pyaudio

from config import cfg
from logger import get_logger
from metrics import metrics

log = get_logger("audio")

CHUNK        = 512
SAMPLE_RATE  = 44100
CHANNELS     = 1
FORMAT       = pyaudio.paInt16
OUTPUT_DEVICE = 0       # ALSA default device (routes to seeed-2mic-voicecard via plughw, supports 44100Hz)

RMS_FLOOR    = 200
RMS_CEILING  = 8000

SILENCE_PRE  = cfg.silence_pre    # 0.02 from bender_config.json
SILENCE_POST = cfg.silence_post   # 0.08 from bender_config.json

log.debug("Audio config: silence_pre=%.3fs, silence_post=%.3fs", SILENCE_PRE, SILENCE_POST)

# Single shared PyAudio instance — never re-created to avoid PortAudio crashes
_pa    = pyaudio.PyAudio()
_stream = None
_lock  = threading.Lock()
_abort = threading.Event()


def get_pa() -> pyaudio.PyAudio:
    """Return the shared PyAudio instance (used by wake_converse for mic stream)."""
    return _pa


def abort():
    """Signal all in-progress playback to stop immediately."""
    _abort.set()


def was_aborted() -> bool:
    """Return True if the last play() call was aborted."""
    return _abort.is_set()


def _silence(duration_s: float) -> bytes:
    n = int(SAMPLE_RATE * duration_s)
    return b'\x00' * (n * 2 * CHANNELS)


def open_session():
    """Open the output stream. Call once after wake word is detected."""
    global _stream
    with _lock:
        if _stream is not None:
            try:
                if _stream.is_active():
                    return
            except Exception:
                pass
            try:
                _stream.close()
            except Exception:
                pass
        _stream = _pa.open(
            format=FORMAT,
            channels=CHANNELS,
            rate=SAMPLE_RATE,
            output=True,
            output_device_index=OUTPUT_DEVICE,
            frames_per_buffer=CHUNK,
        )
        # Warm up the DAC with a brief silence burst
        _stream.write(_silence(0.1))


def close_session():
    """Close the output stream. Call after session ends."""
    global _stream
    with _lock:
        if _stream is not None:
            try:
                _stream.write(_silence(0.1))  # drain tail
                _stream.stop_stream()
                _stream.close()
            except Exception:
                pass
            _stream = None


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

def play(wav_path: str, on_chunk=None, on_done=None):
    """
    Play a WAV file.
    open_session() must be called first.

    Args:
        wav_path:  Path to the WAV file to play.
        on_chunk:  Optional callback called for each audio chunk with a
                   normalised amplitude value in [0.0, 1.0].
        on_done:   Optional callback called once after playback finishes
                   (always invoked outside _lock).
    """
    with metrics.timer("audio_play"):
        with _lock:
            _abort.clear()
            if _stream is None or not _stream.is_active():
                # Fallback: reopen if session stream was lost
                open_session()

            _stream.write(_silence(SILENCE_PRE))

            with wave.open(wav_path, 'rb') as wf:
                sw = wf.getsampwidth()
                data = wf.readframes(CHUNK)
                while data:
                    if _abort.is_set():
                        log.info("Playback aborted: %s", wav_path)
                        break
                    _stream.write(data)
                    if on_chunk:
                        on_chunk(rms_to_ratio(rms(data, sw)))
                    data = wf.readframes(CHUNK)

            if not _abort.is_set():
                _stream.write(_silence(SILENCE_POST))

    if on_done:
        on_done()


def play_oneshot(wav_path: str, on_chunk=None, on_done=None):
    """Open stream, play clip, close stream. For use outside a session.
    Thread-safe — blocks behind _lock if a session is active.

    Args:
        wav_path:  Path to the WAV file to play.
        on_chunk:  Optional callback called for each audio chunk with a
                   normalised amplitude value in [0.0, 1.0].
        on_done:   Optional callback called once after playback finishes
                   (always invoked outside _lock).
    """
    with _lock:
        _abort.clear()
        was_open = _stream is not None
        if was_open:
            try:
                was_open = _stream.is_active()
            except Exception:
                was_open = False

        if not was_open:
            stream = _pa.open(
                format=FORMAT, channels=CHANNELS, rate=SAMPLE_RATE,
                output=True, output_device_index=OUTPUT_DEVICE,
                frames_per_buffer=CHUNK,
            )
        else:
            stream = _stream

        try:
            stream.write(_silence(SILENCE_PRE))
            with wave.open(wav_path, 'rb') as wf:
                sw = wf.getsampwidth()
                data = wf.readframes(CHUNK)
                while data:
                    if _abort.is_set():
                        log.info("Playback aborted: %s", wav_path)
                        break
                    stream.write(data)
                    if on_chunk:
                        on_chunk(rms_to_ratio(rms(data, sw)))
                    data = wf.readframes(CHUNK)
            if not _abort.is_set():
                stream.write(_silence(SILENCE_POST))
        finally:
            if not was_open:
                stream.stop_stream()
                stream.close()
    if on_done:
        on_done()


def play_stream_oneshot(wav_iter, on_chunk=None, on_done=None):
    """Open stream, play WAV clips from an iterator back-to-back, close stream.
    For use outside a session (camera responses, passive vision). Thread-safe —
    blocks behind _lock if a session is active. Unlinks each WAV after playing.
    Closes the generator on abort to trigger cleanup of unconsumed futures.

    Args:
        wav_iter:  Iterator yielding WAV file paths (e.g. speak_streaming()).
        on_chunk:  Optional callback(amplitude: float) per chunk, value in [0.0, 1.0].
        on_done:   Optional callback called once after all clips finish (or abort).
    """
    gen = iter(wav_iter)
    with _lock:
        _abort.clear()
        was_open = _stream is not None
        if was_open:
            try:
                was_open = _stream.is_active()
            except Exception:
                was_open = False

        if not was_open:
            stream = _pa.open(
                format=FORMAT, channels=CHANNELS, rate=SAMPLE_RATE,
                output=True, output_device_index=OUTPUT_DEVICE,
                frames_per_buffer=CHUNK,
            )
        else:
            stream = _stream

        try:
            stream.write(_silence(SILENCE_PRE))
            for wav_path in gen:
                if _abort.is_set():
                    try:
                        os.unlink(wav_path)
                    except OSError:
                        pass
                    if hasattr(gen, "close"):
                        gen.close()  # triggers BaseException cleanup in speak_streaming
                    break
                try:
                    with wave.open(wav_path, 'rb') as wf:
                        sw = wf.getsampwidth()
                        data = wf.readframes(CHUNK)
                        while data:
                            if _abort.is_set():
                                break
                            stream.write(data)
                            if on_chunk:
                                on_chunk(rms_to_ratio(rms(data, sw)))
                            data = wf.readframes(CHUNK)
                finally:
                    try:
                        os.unlink(wav_path)
                    except OSError:
                        pass
            if not _abort.is_set():
                stream.write(_silence(SILENCE_POST))
        finally:
            if not was_open:
                stream.stop_stream()
                stream.close()
    if on_done:
        on_done()


def play_stream(wav_iter, on_chunk=None, on_done=None):
    """
    Play WAV files from an iterator, back-to-back with no gap between sentences.
    Starts playing as soon as the first WAV is yielded; remaining sentences play
    as they arrive. Handles abort cleanly. Caller's iterator should yield temp
    file paths; this function unlinks each after playing.

    open_session() must be called first.
    """
    with metrics.timer("audio_play"):
        with _lock:
            _abort.clear()
            if _stream is None or not _stream.is_active():
                open_session()

            _stream.write(_silence(SILENCE_PRE))

            for wav_path in wav_iter:
                if _abort.is_set():
                    # Drain remaining paths and clean up
                    try:
                        os.unlink(wav_path)
                    except OSError:
                        pass
                    for remaining in wav_iter:
                        try:
                            os.unlink(remaining)
                        except OSError:
                            pass
                    break

                try:
                    with wave.open(wav_path, 'rb') as wf:
                        sw = wf.getsampwidth()
                        data = wf.readframes(CHUNK)
                        while data:
                            if _abort.is_set():
                                break
                            _stream.write(data)
                            if on_chunk:
                                on_chunk(rms_to_ratio(rms(data, sw)))
                            data = wf.readframes(CHUNK)
                finally:
                    try:
                        os.unlink(wav_path)
                    except OSError:
                        pass

            if not _abort.is_set():
                _stream.write(_silence(SILENCE_POST))

    if on_done:
        on_done()

