#!/usr/bin/env python3
"""
Speech-to-text for Bender using faster-whisper + webrtcvad.

Usage (standalone test):
    python3 scripts/stt.py

Returns the transcribed text to stdout, or '' on timeout/silence.
"""

import os
import struct
import wave
import tempfile
import collections
import time

import webrtcvad
from faster_whisper import WhisperModel

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SAMPLE_RATE    = 16000    # Hz — required by webrtcvad and whisper
CHANNELS       = 1
FRAME_MS       = 30       # VAD frame size in ms (10/20/30 supported)
FRAME_BYTES    = int(SAMPLE_RATE * FRAME_MS / 1000) * 2  # 16-bit samples
VAD_AGGRESSIVENESS = 2    # 0 (least aggressive) to 3 (most)
SILENCE_FRAMES = 50       # ~1.5s silence at 30ms/frame to end utterance
MAX_RECORD_S   = 15       # hard cap per utterance

WHISPER_MODEL  = "tiny.en"
AUDIO_DEVICE   = None     # None = system default

_model = None  # lazy-loaded


def _load_model():
    global _model
    if _model is None:
        _model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    return _model


# ---------------------------------------------------------------------------
# Recording with VAD
# ---------------------------------------------------------------------------

def _record_utterance() -> bytes:
    """Record from mic until ~1.5s silence or hard cap. Returns raw PCM bytes."""
    import pyaudio  # imported here so stt is still importable without pyaudio

    vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)
    pa = pyaudio.PyAudio()

    stream = pa.open(
        format=pyaudio.paInt16,
        channels=CHANNELS,
        rate=SAMPLE_RATE,
        input=True,
        frames_per_buffer=int(SAMPLE_RATE * FRAME_MS / 1000),
        input_device_index=AUDIO_DEVICE,
    )

    frames = []
    ring = collections.deque(maxlen=SILENCE_FRAMES)
    started = False
    start_time = time.time()
    silent_count = 0

    try:
        while True:
            if time.time() - start_time > MAX_RECORD_S:
                break

            data = stream.read(int(SAMPLE_RATE * FRAME_MS / 1000),
                               exception_on_overflow=False)
            frames.append(data)
            is_speech = vad.is_speech(data, SAMPLE_RATE)

            if is_speech:
                started = True
                silent_count = 0
            else:
                if started:
                    silent_count += 1
                    if silent_count >= SILENCE_FRAMES:
                        break
    finally:
        stream.stop_stream()
        stream.close()
        pa.terminate()

    return b"".join(frames)


def _pcm_to_wav(pcm: bytes) -> str:
    """Write raw PCM to a temp WAV file, return path."""
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    with wave.open(tmp.name, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm)
    return tmp.name


# ---------------------------------------------------------------------------
# Transcribe
# ---------------------------------------------------------------------------

def transcribe(audio_path: str) -> str:
    """Transcribe a WAV file using faster-whisper. Returns text string."""
    model = _load_model()
    segments, _ = model.transcribe(audio_path, language="en", beam_size=1)
    return " ".join(s.text for s in segments).strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def listen_and_transcribe() -> str:
    """
    Record one utterance and return the transcribed text.
    Returns empty string if nothing is heard.
    """
    pcm = _record_utterance()
    if len(pcm) < FRAME_BYTES * 3:
        return ""
    wav_path = _pcm_to_wav(pcm)
    try:
        text = transcribe(wav_path)
    finally:
        os.unlink(wav_path)
    return text


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Say something... (up to 15s, stops after 1.5s silence)")
    text = listen_and_transcribe()
    if text:
        print(f"You said: {text}")
    else:
        print("(nothing heard)")
