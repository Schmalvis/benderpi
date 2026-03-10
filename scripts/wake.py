#!/usr/bin/env python3
"""
wake.py — Hey Bender wake word detector (Porcupine)

Usage:
    python3 wake.py

Requires:
    - PORCUPINE_ACCESS_KEY env var (or .env in project root)
    - hey-bender.ppn in the same directory (trained at console.picovoice.ai)
    - pvporcupine, pvrecorder  (pip3 install pvporcupine pvrecorder)

On detection: plays a random greeting from ../speech/greetings.txt
              with real-time LED amplitude visualisation
"""

import os
import random
import pvporcupine
from pvrecorder import PvRecorder
import audio
import leds

# --- Config ---
_env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
if os.path.exists(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            if _line.startswith("PORCUPINE_ACCESS_KEY="):
                os.environ["PORCUPINE_ACCESS_KEY"] = _line.strip().split("=", 1)[1]

ACCESS_KEY     = os.environ.get("PORCUPINE_ACCESS_KEY", "YOUR_ACCESS_KEY_HERE")
KEYWORD_PATH   = os.path.join(os.path.dirname(__file__), "hey-bender.ppn")
SPEECH_DIR     = os.path.join(os.path.dirname(__file__), "..", "speech", "wav")
GREETINGS_FILE = os.path.join(os.path.dirname(__file__), "..", "speech", "greetings.txt")


def load_greetings():
    if not os.path.exists(GREETINGS_FILE):
        raise SystemExit(f"Greetings file not found: {GREETINGS_FILE}")
    clips = []
    with open(GREETINGS_FILE) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                clips.append(line)
    if not clips:
        raise SystemExit("No greetings defined in greetings.txt")
    return clips


def play_greeting(greetings):
    clip = random.choice(greetings)
    path = os.path.join(SPEECH_DIR, clip)
    if os.path.exists(path):
        audio.play(path)
    else:
        print(f"[warn] clip not found: {path}")


def main():
    if ACCESS_KEY == "YOUR_ACCESS_KEY_HERE":
        raise SystemExit("Set PORCUPINE_ACCESS_KEY in .env or as an env var")

    if not os.path.exists(KEYWORD_PATH):
        raise SystemExit(f"Wake word model not found: {KEYWORD_PATH}\n"
                         "Train 'Hey Bender' at console.picovoice.ai and download the .ppn for Raspberry Pi")

    greetings = load_greetings()
    print(f"[bender] Loaded {len(greetings)} greeting(s) from greetings.txt")

    porcupine = pvporcupine.create(
        access_key=ACCESS_KEY,
        keyword_paths=[KEYWORD_PATH],
    )

    recorder = PvRecorder(frame_length=porcupine.frame_length)
    recorder.start()

    print("[bender] Listening for 'Hey Bender'... (Ctrl+C to stop)")

    try:
        while True:
            pcm = recorder.read()
            result = porcupine.process(pcm)
            if result >= 0:
                print("[bender] Wake word detected!")
                recorder.stop()
                play_greeting(greetings)
                recorder.start()
    except KeyboardInterrupt:
        print("\n[bender] Stopped.")
    finally:
        recorder.stop()
        recorder.delete()
        porcupine.delete()
        leds.all_off()


if __name__ == "__main__":
    main()
