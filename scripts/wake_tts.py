#!/usr/bin/env python3
"""
wake_tts.py — Hey Bender wake word detector using Piper TTS responses.

Drop-in parallel to wake.py. Uses the same Porcupine wake word detection
but generates speech via the fine-tuned Bender TTS model instead of
playing pre-recorded clips.

Responses are drawn from speech/tts_lines.txt (one line per entry, # = comment).
Falls back to speech/metadata.csv transcripts if tts_lines.txt is absent.

Switch between modes:
    scripts/switch_mode.sh
"""

import os
import random
import tempfile
import pvporcupine
from pvrecorder import PvRecorder
import audio
import leds
import tts_generate

# --- Config ---
_env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
if os.path.exists(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            if _line.startswith("PORCUPINE_ACCESS_KEY="):
                os.environ["PORCUPINE_ACCESS_KEY"] = _line.strip().split("=", 1)[1]

ACCESS_KEY     = os.environ.get("PORCUPINE_ACCESS_KEY", "YOUR_ACCESS_KEY_HERE")
KEYWORD_PATH   = os.path.join(os.path.dirname(__file__), "hey-bender.ppn")
TTS_LINES_FILE = os.path.join(os.path.dirname(__file__), "..", "speech", "tts_lines.txt")
METADATA_FILE  = os.path.join(os.path.dirname(__file__), "..", "speech", "metadata.csv")


def load_lines():
    """Load TTS response lines. Prefers tts_lines.txt, falls back to metadata.csv."""
    lines = []

    if os.path.exists(TTS_LINES_FILE):
        with open(TTS_LINES_FILE) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    lines.append(line)
        if lines:
            print(f"[bender-tts] Loaded {len(lines)} lines from tts_lines.txt")
            return lines

    if os.path.exists(METADATA_FILE):
        with open(METADATA_FILE) as f:
            for line in f:
                parts = line.strip().split("|", 1)
                if len(parts) == 2:
                    lines.append(parts[1])
        print(f"[bender-tts] Loaded {len(lines)} lines from metadata.csv (fallback)")
        return lines

    raise SystemExit("No TTS lines found — create speech/tts_lines.txt")


def play_tts(lines):
    text = random.choice(lines)
    print(f"[bender-tts] Generating: {text}")
    try:
        wav_path = tts_generate.speak(text)
        audio.play(wav_path)
    except Exception as e:
        print(f"[bender-tts] TTS error: {e}")
    finally:
        try:
            os.unlink(wav_path)
        except Exception:
            pass


def main():
    if ACCESS_KEY == "YOUR_ACCESS_KEY_HERE":
        raise SystemExit("Set PORCUPINE_ACCESS_KEY in .env or as an env var")

    if not os.path.exists(KEYWORD_PATH):
        raise SystemExit(f"Wake word model not found: {KEYWORD_PATH}")

    lines = load_lines()

    porcupine = pvporcupine.create(
        access_key=ACCESS_KEY,
        keyword_paths=[KEYWORD_PATH],
    )

    recorder = PvRecorder(frame_length=porcupine.frame_length)
    recorder.start()

    print("[bender-tts] Listening for 'Hey Bender'... (Ctrl+C to stop)")

    try:
        while True:
            pcm = recorder.read()
            result = porcupine.process(pcm)
            if result >= 0:
                print("[bender-tts] Wake word detected!")
                recorder.stop()
                play_tts(lines)
                recorder.start()
    except KeyboardInterrupt:
        print("\n[bender-tts] Stopped.")
    finally:
        recorder.stop()
        recorder.delete()
        porcupine.delete()
        leds.all_off()


if __name__ == "__main__":
    main()
