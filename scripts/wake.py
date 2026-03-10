#!/usr/bin/env python3
"""
wake.py — Hey Bender wake word detector (Porcupine)

Usage:
    python3 wake.py

Requires:
    - PORCUPINE_ACCESS_KEY env var (or set ACCESS_KEY below)
    - hey_bender.ppn in the same directory (trained at console.picovoice.ai)
    - pvporcupine, pvrecorder  (pip3 install pvporcupine pvrecorder)
    - alsa-utils  (aplay)

On detection: plays a random greeting from ../speech/wav/
"""

import os
import random
import subprocess
import struct
import pvporcupine
from pvrecorder import PvRecorder

# --- Config ---
ACCESS_KEY   = os.environ.get("PORCUPINE_ACCESS_KEY", "YOUR_ACCESS_KEY_HERE")
KEYWORD_PATH = os.path.join(os.path.dirname(__file__), "hey_bender.ppn")
SPEECH_DIR   = os.path.join(os.path.dirname(__file__), "..", "speech", "wav")

GREETINGS = [
    "hello.wav",
    "hellopeasants.wav",
    "imbender.wav",
    "yo.wav",
    "heyheresanidea.wav",
    "iknowwhatthisisabout.wav",
]

def play_greeting():
    clip = random.choice(GREETINGS)
    path = os.path.join(SPEECH_DIR, clip)
    if os.path.exists(path):
        subprocess.run(["aplay", "-q", path])
    else:
        print(f"[warn] clip not found: {path}")

def main():
    if ACCESS_KEY == "YOUR_ACCESS_KEY_HERE":
        raise SystemExit("Set PORCUPINE_ACCESS_KEY env var or edit ACCESS_KEY in wake.py")

    if not os.path.exists(KEYWORD_PATH):
        raise SystemExit(f"Wake word model not found: {KEYWORD_PATH}\n"
                         "Train 'Hey Bender' at console.picovoice.ai and download the .ppn for linux/arm64")

    porcupine = pvporcupine.create(
        access_key=ACCESS_KEY,
        keyword_paths=[KEYWORD_PATH],
    )

    recorder = PvRecorder(frame_length=porcupine.frame_length)
    recorder.start()

    print(f"[bender] Listening for 'Hey Bender'... (Ctrl+C to stop)")

    try:
        while True:
            pcm = recorder.read()
            result = porcupine.process(pcm)
            if result >= 0:
                print("[bender] Wake word detected!")
                recorder.stop()
                play_greeting()
                recorder.start()
    except KeyboardInterrupt:
        print("\n[bender] Stopped.")
    finally:
        recorder.stop()
        recorder.delete()
        porcupine.delete()

if __name__ == "__main__":
    main()
