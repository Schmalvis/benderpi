#!/usr/bin/env python3
"""
tts_generate.py — Piper TTS inference wrapper for Bender voice.

Usage:
    import tts_generate
    wav_path = tts_generate.speak("Bite my shiny metal ass!")
    # wav_path is a temp file — caller is responsible for playing and cleanup
"""

import os
import subprocess
import tempfile

PIPER_BIN  = os.path.join(os.path.dirname(__file__), "..", "piper", "piper")
MODEL_PATH = os.path.join(os.path.dirname(__file__), "..", "models", "bender.onnx")


def speak(text: str) -> str:
    """Generate TTS audio for text. Returns path to a temp WAV file."""
    if not os.path.exists(PIPER_BIN):
        raise FileNotFoundError(f"Piper binary not found: {PIPER_BIN}")
    if not os.path.exists(MODEL_PATH):
        raise FileNotFoundError(f"Bender model not found: {MODEL_PATH}")

    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()

    piper_dir = os.path.dirname(PIPER_BIN)
    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = piper_dir + ":" + env.get("LD_LIBRARY_PATH", "")

    result = subprocess.run(
        [PIPER_BIN, "--model", MODEL_PATH, "--output_file", tmp.name],
        input=text.encode(),
        capture_output=True,
        env=env,
    )

    if result.returncode != 0:
        os.unlink(tmp.name)
        raise RuntimeError(f"Piper failed: {result.stderr.decode()}")

    return tmp.name
