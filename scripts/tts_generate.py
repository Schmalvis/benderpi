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
import re
import subprocess
import tempfile
import wave
import numpy as np

from logger import get_logger
from metrics import metrics

log = get_logger("tts")

from config import cfg

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

    # De-ess: gently attenuate harsh high-frequency content above 7kHz
    from scipy.signal import butter, sosfilt
    nyq = TARGET_RATE / 2
    sos = butter(2, 7000 / nyq, btype='high', output='sos')
    highs = sosfilt(sos, samples.astype(np.float64)).astype(np.float32)
    samples = samples - highs * 0.4  # reduce >7kHz by ~4dB

    # Clip and convert back to int16
    samples = np.clip(samples, -32768, 32767).astype(np.int16)

    with wave.open(out_path, 'wb') as wf:
        wf.setnchannels(n_channels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(TARGET_RATE)
        wf.writeframes(samples.tobytes())


def _speak_single(text: str) -> str:
    """
    Generate TTS audio for a single sentence. Returns path to a temp WAV file at 44100Hz.
    Caller is responsible for playing and cleanup.
    """
    with metrics.timer("tts_generate"):
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
            [
            PIPER_BIN,
            "--model", MODEL_PATH,
            "--output_file", raw_tmp.name,
            "--length_scale", str(cfg.speech_rate),
            "--noise_scale", str(cfg.tts_noise_scale),
            "--noise_scale_w", str(cfg.tts_noise_scale_w),
        ],
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



_SENTENCE_RE = re.compile(r'(?<=[.!?])\s+')

def _preprocess_text(text: str) -> str:
    """Normalise text for natural TTS delivery."""
    # Strip markdown bold/italic
    text = re.sub(r'\*+([^*]+)\*+', r'\1', text)
    # Em-dash and en-dash → comma pause
    text = re.sub(r'\s*[–—]\s*', ', ', text)
    # Ellipsis → pause
    text = text.replace('...', ', ')
    # Multiple spaces
    text = re.sub(r'  +', ' ', text)
    return text.strip()


def speak(text: str) -> str:
    """
    Generate TTS audio for text, splitting on sentence boundaries for
    more natural prosody. Returns path to a concatenated temp WAV at 44100Hz.
    Caller is responsible for playing and cleanup.
    """
    text = _preprocess_text(text)
    sentences = [s.strip() for s in _SENTENCE_RE.split(text) if s.strip()]
    if len(sentences) <= 1:
        return _speak_single(text)

    # Generate sentences in parallel — each is an independent Piper subprocess
    import wave
    from concurrent.futures import ThreadPoolExecutor
    parts = []
    try:
        with ThreadPoolExecutor(max_workers=min(len(sentences), 3)) as pool:
            futures = [pool.submit(_speak_single, s) for s in sentences]
            parts = [f.result() for f in futures]  # preserves order, total time = max(sentence times)

        # Read all WAVs and concatenate frames
        out_tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        out_tmp.close()
        frames = b""
        params = None
        for p in parts:
            with wave.open(p, 'rb') as wf:
                if params is None:
                    params = wf.getparams()
                frames += wf.readframes(wf.getnframes())

        with wave.open(out_tmp.name, 'wb') as wf:
            wf.setparams(params)
            wf.writeframes(frames)
        return out_tmp.name
    finally:
        for p in parts:
            try:
                os.unlink(p)
            except OSError:
                pass


def warm_up():
    """Pre-warm Piper by running a dummy synthesis. Call at service start."""
    log.info("Warming up Piper TTS...")
    try:
        wav = speak("test")
        os.unlink(wav)
        log.info("Piper warm-up complete")
    except Exception as e:
        log.warning("Piper warm-up failed (expected if not on Pi): %s", e)
