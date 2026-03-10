#!/usr/bin/env python3
"""
audio.py — WAV playback with real-time LED amplitude visualisation
"""

import wave
import pyaudio
import numpy as np
import leds

CHUNK       = 512   # samples per buffer (~11ms at 44100Hz)
RMS_FLOOR   = 200   # ignore noise below this level
RMS_CEILING = 8000  # RMS value that maps to full LED bar


def rms(data, sample_width):
    """Calculate RMS amplitude from raw audio bytes."""
    dtype = np.int16 if sample_width == 2 else np.int8
    samples = np.frombuffer(data, dtype=dtype).astype(np.float32)
    return float(np.sqrt(np.mean(samples ** 2))) if len(samples) else 0.0


def rms_to_ratio(value):
    """Map RMS value to a 0.0–1.0 brightness ratio."""
    clamped = max(0, value - RMS_FLOOR)
    return min(clamped / (RMS_CEILING - RMS_FLOOR), 1.0)


def play(wav_path):
    """Play a WAV file and drive LEDs in sync with amplitude."""
    pa = pyaudio.PyAudio()

    with wave.open(wav_path, 'rb') as wf:
        stream = pa.open(
            format=pa.get_format_from_width(wf.getsampwidth()),
            channels=wf.getnchannels(),
            rate=wf.getframerate(),
            output=True,
        )

        data = wf.readframes(CHUNK)
        while data:
            stream.write(data)
            leds.set_level(rms_to_ratio(rms(data, wf.getsampwidth())))
            data = wf.readframes(CHUNK)

        stream.stop_stream()
        stream.close()

    pa.terminate()
    leds.all_off()
