#!/usr/bin/env python3
"""
tts_generate.py — Piper TTS inference wrapper for Bender voice.

Post-processes Piper output:
  - Resamples from 22050Hz → 44100Hz to match real Bender clips
  - Adds 50ms silence padding at start and end to prevent audio pops

Usage:
    import tts_generate
    wav_path = tts_generate.speak("Bite my shiny metal ass!")
    # wav_path is a temp file — caller is responsible for playing and cleanup
"""

import os
import subprocess
import tempfile
import wave
import numpy as np

PIPER_BIN  = os.path.join(os.path.dirname(__file__), "..", "piper", "piper")
MODEL_PATH = os.path.join(os.path.dirname(__file__), "..", "models", "bender.onnx")

TARGET_RATE  = 44100   # match real Bender clips
SILENCE_PRE  = 0.0    # audio.py adds pre-silence for all clips
SILENCE_POST = 0.0    # audio.py adds post-silence for all clips


def _resample_and_pad(in_path: str, out_path: str):
    """Resample WAV to TARGET_RATE and pad with silence."""
    from scipy.signal import resample_poly
    from math import gcd

    with wave.open(in_path, 'rb') as wf:
        src_rate   = wf.getframerate()
        n_channels = wf.getnchannels()
        sampwidth  = wf.getsampwidth()
        raw        = wf.readframes(wf.getnframes())

    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32)

    # Resample if needed
    if src_rate != TARGET_RATE:
        g  = gcd(TARGET_RATE, src_rate)
        up = TARGET_RATE // g
        dn = src_rate    // g
        samples = resample_poly(samples, up, dn)

    # Silence padding (samples)
    pre_pad  = np.zeros(int(TARGET_RATE * SILENCE_PRE),  dtype=np.float32)
    post_pad = np.zeros(int(TARGET_RATE * SILENCE_POST), dtype=np.float32)
    samples  = np.concatenate([pre_pad, samples, post_pad])

    # Clip and convert back to int16
    samples = np.clip(samples, -32768, 32767).astype(np.int16)

    with wave.open(out_path, 'wb') as wf:
        wf.setnchannels(n_channels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(TARGET_RATE)
        wf.writeframes(samples.tobytes())


def speak(text: str) -> str:
    """
    Generate TTS audio for text. Returns path to a temp WAV file at 44100Hz.
    Caller is responsible for playing and cleanup.
    """
    if not os.path.exists(PIPER_BIN):
        raise FileNotFoundError(f"Piper binary not found: {PIPER_BIN}")
    if not os.path.exists(MODEL_PATH):
        raise FileNotFoundError(f"Bender model not found: {MODEL_PATH}")

    # Piper writes raw 22050Hz output
    raw_tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    raw_tmp.close()

    piper_dir = os.path.dirname(PIPER_BIN)
    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = piper_dir + ":" + env.get("LD_LIBRARY_PATH", "")

    result = subprocess.run(
        [PIPER_BIN, "--model", MODEL_PATH, "--output_file", raw_tmp.name],
        input=text.encode(),
        capture_output=True,
        env=env,
    )

    if result.returncode != 0:
        os.unlink(raw_tmp.name)
        raise RuntimeError(f"Piper failed: {result.stderr.decode()}")

    # Post-process: resample + pad → final temp file
    out_tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    out_tmp.close()
    try:
        _resample_and_pad(raw_tmp.name, out_tmp.name)
    finally:
        os.unlink(raw_tmp.name)

    return out_tmp.name
