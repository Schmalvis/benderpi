#!/usr/bin/env python3
"""
Speech-to-text for Bender.

Backends (in priority order):
  1. Hailo Speech2Text (Whisper-Small on Hailo-10H NPU) — primary
  2. faster-whisper CPU (base.en) — fallback if Hailo unavailable

Usage (standalone test):
    python3 scripts/stt.py

Returns the transcribed text to stdout, or '' on timeout/silence.
"""

import os
import wave
import tempfile
import collections
import time

import numpy as np
import webrtcvad

import audio as audio_mod
from config import cfg
from logger import get_logger
from metrics import metrics

log = get_logger("stt")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SAMPLE_RATE    = 16000    # Hz — required by webrtcvad and whisper
CHANNELS       = 1
FRAME_MS       = 30       # VAD frame size in ms (10/20/30 supported)
FRAME_BYTES    = int(SAMPLE_RATE * FRAME_MS / 1000) * 2  # 16-bit samples
AUDIO_DEVICE   = None     # None = system default

# Hailo NPU backend (Whisper-Small HEF — primary)
WHISPER_HEF        = "/usr/local/hailo/resources/models/hailo10h/Whisper-Small.hef"

WHISPER_HALLUCINATIONS = {
    "thank you", "thanks for watching", "subscribe",
    "like and subscribe", "thanks for listening",
    "please subscribe", "thank you for watching",
    "you", "the", "i", "a", "so", "okay",
}

# ---------------------------------------------------------------------------
# Backend init
# ---------------------------------------------------------------------------

_backend   = None   # "hailo" | "cpu"
_vdevice   = None   # Hailo VDevice (held open for lifetime of process)
_s2t       = None   # Hailo Speech2Text instance
_cpu_model = None   # faster-whisper fallback


def _load_model():
    """Initialise Hailo Whisper-Small as primary STT, falling back to CPU on failure."""
    global _backend, _vdevice, _s2t, _cpu_model

    if _backend is not None:
        return

    # Hailo primary
    if os.path.exists(WHISPER_HEF):
        try:
            from hailo_platform import VDevice
            from hailo_platform.genai import Speech2Text, Speech2TextTask  # noqa: F401
            _params = VDevice.create_params()
            _params.group_id = "SHARED"
            _vdevice = VDevice(_params)
            _vdevice.__enter__()
            _s2t = Speech2Text(_vdevice, WHISPER_HEF)
            _s2t.__enter__()
            _backend = "hailo"
            log.info("STT backend: Hailo Speech2Text (Whisper-Small on Hailo-10H)")
            return
        except Exception as e:
            log.warning("Hailo STT init failed (%s) — falling back to CPU", e)
            if _vdevice is not None:
                try:
                    _vdevice.__exit__(None, None, None)
                except Exception:
                    pass
                _vdevice = None
            _s2t = None

    # CPU fallback
    try:
        from faster_whisper import WhisperModel
        _cpu_model = WhisperModel(cfg.whisper_model, device="cpu", compute_type="int8")
        _backend = "cpu"
        log.info("STT backend: faster-whisper CPU (%s)", cfg.whisper_model)
        return
    except Exception as e:
        log.warning("faster-whisper init failed (%s)", e)

    raise RuntimeError("No STT backend available")


def _active_model_name() -> str:
    return "whisper-small-hailo" if _backend == "hailo" else cfg.whisper_model


# ---------------------------------------------------------------------------
# Transcription helpers
# ---------------------------------------------------------------------------

def _transcribe_array(audio_array: np.ndarray) -> str:
    """Transcribe a float32 numpy array. Assumes model already loaded."""
    if _backend == "hailo":
        from hailo_platform.genai import Speech2TextTask
        return _s2t.generate_all_text(
            audio_data=audio_array,
            task=Speech2TextTask.TRANSCRIBE,
            language="en",
        ).strip()
    else:
        segments, _ = _cpu_model.transcribe(audio_array, language="en", beam_size=1)
        return " ".join(s.text for s in segments).strip()


def _wav_to_array(wav_path: str) -> np.ndarray:
    """Load a WAV file into a float32 numpy array normalised to [-1, 1]."""
    with wave.open(wav_path, "rb") as wf:
        pcm = wf.readframes(wf.getnframes())
    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0


def _filter_hallucination(text: str, source: str = "") -> str:
    """Return '' if text looks like a Whisper hallucination."""
    cleaned = text.lower().strip().rstrip(".!?,")
    if cleaned in WHISPER_HALLUCINATIONS:
        log.warning("Whisper hallucination filtered: %r%s", text,
                    f" ({source})" if source else "")
        metrics.count("stt_hallucination", text=text, source=source or "mic")
        return ""
    return text


# ---------------------------------------------------------------------------
# Recording with VAD
# ---------------------------------------------------------------------------

def _record_utterance() -> bytes:
    """Record from mic until ~1.5s silence or hard cap. Returns raw PCM bytes."""
    import pyaudio

    vad = webrtcvad.Vad(cfg.vad_aggressiveness)
    pa  = audio_mod.get_pa()  # shared instance — DO NOT terminate

    stream = pa.open(
        format=pyaudio.paInt16,
        channels=CHANNELS,
        rate=SAMPLE_RATE,
        input=True,
        frames_per_buffer=int(SAMPLE_RATE * FRAME_MS / 1000),
        input_device_index=AUDIO_DEVICE,
    )

    frames       = []
    started      = False
    start_time   = time.time()
    silent_count = 0

    try:
        while True:
            if time.time() - start_time > cfg.max_record_seconds:
                break
            data     = stream.read(int(SAMPLE_RATE * FRAME_MS / 1000),
                                   exception_on_overflow=False)
            frames.append(data)
            is_speech = vad.is_speech(data, SAMPLE_RATE)
            if is_speech:
                started      = True
                silent_count = 0
            elif started:
                silent_count += 1
                if silent_count >= cfg.silence_frames:
                    break
    finally:
        stream.stop_stream()
        stream.close()

    return b"".join(frames)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def transcribe(audio_path: str) -> str:
    """Transcribe a WAV file. Returns text string."""
    _load_model()
    audio_array = _wav_to_array(audio_path)
    return _transcribe_array(audio_array)


def listen_and_transcribe() -> str:
    """Record one utterance and return the transcribed text."""
    _load_model()

    with metrics.timer("stt_record"):
        pcm = _record_utterance()

    if len(pcm) < FRAME_BYTES * 3:
        metrics.count("stt_empty", pcm_bytes=len(pcm))
        return ""

    audio_array = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0

    with metrics.timer("stt_transcribe", model=_active_model_name()):
        text = _transcribe_array(audio_array)

    return _filter_hallucination(text)


def transcribe_file(wav_path: str) -> str:
    """Transcribe a pre-recorded WAV file (e.g. uploaded via web UI)."""
    _load_model()
    audio_array = _wav_to_array(wav_path)
    with metrics.timer("stt_transcribe", model=_active_model_name(), source="file"):
        text = _transcribe_array(audio_array)
    return _filter_hallucination(text, source="file")


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    _load_model()
    print(f"Backend: {_backend}  ({_active_model_name()})")
    print("Say something... (up to 15s, stops after 1.5s silence)")
    text = listen_and_transcribe()
    if text:
        print(f"You said: {text}")
    else:
        print("(nothing heard)")
