#!/usr/bin/env python3
"""Capture real-voice training samples for the "hey bender" wake word.

WHY THIS RUNS ON THE PI, NOT A LAPTOP
-------------------------------------
hey_bender_v0.1 was trained entirely on synthetic speech (train_hey_bender.py
drives piper-sample-generator). Measured 2026-08-01 it scores 0.97 on a
synthetic "hey bender" and 0.002-0.27 on a live human -- it has never heard a
real person say the phrase. Recording anywhere other than the device would
close one domain gap and open another: these samples must come through the same
WM8960 mic, the same 29dB input boost + 12dB capture gain, the same dsnoop
resampling and the same room. That acoustic path IS the training signal.

USAGE
    # positives, prompted, one speaker at a time
    venv/bin/python scripts/capture_wake_samples.py --speaker martin

    # hard negatives: similar phrases that must NOT wake him
    venv/bin/python scripts/capture_wake_samples.py --speaker martin --mode hard_negative

    # background audio (TV, kitchen, conversation) as plain negatives
    venv/bin/python scripts/capture_wake_samples.py --mode ambient --minutes 20

Each positive is scored against the *current* model as it is captured, so the
session doubles as a recall measurement: the "current model scored" tally at the
end is the before-number the retrain has to beat.

Output layout (gitignored -- audio of the household never enters git):
    data/wake_samples/positive/<speaker>/<condition>_<nnn>.wav
    data/wake_samples/hard_negative/<speaker>/<phrase>_<nnn>.wav
    data/wake_samples/ambient/<nnn>.wav
    data/wake_samples/manifest.jsonl
"""
import argparse
import json
import os
import subprocess
import sys
import time
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import cfg  # noqa: E402

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_ROOT = os.path.join(BASE_DIR, "data", "wake_samples")
RATE = 16000
FRAME_MS = 30
CLIP_S = 2.0          # openWakeWord positives sit comfortably inside 2s
RECORD_S = 3.0        # record longer, then trim to the voiced region
PAD_MS = 200          # keep this much either side of the voiced span

# Conditions worth varying. The v0.1 failure is a generalisation failure, so
# breadth across the acoustic envelope matters far more than raw sample count.
POSITIVE_CONDITIONS = [
    ("close_normal",    "~0.5m away, normal speaking voice"),
    ("mid_normal",      "~1.5m away, normal speaking voice"),
    ("far_normal",      "~3m away (across the room), normal voice"),
    ("mid_quiet",       "~1.5m away, quietly - almost muttering"),
    ("mid_loud",        "~1.5m away, raised voice"),
    ("mid_fast",        "~1.5m away, said quickly - 'heybender'"),
    ("mid_slow",        "~1.5m away, drawn out - 'heeey benderrr'"),
    ("off_axis",        "~1.5m away, facing AWAY from the device"),
    ("with_background", "~1.5m away, normal voice, TV or music playing"),
    ("moving",          "walking past the device as you say it"),
]

# Phrases that share phonetics with the wake word. Without these, adding real
# positives raises recall and false positives together.
HARD_NEGATIVE_PHRASES = [
    "hey there", "hey friend", "bender", "hey bend", "hey Brenda",
    "hey vendor", "okay then", "hey bender's", "play defender",
]


def _service_active() -> bool:
    try:
        out = subprocess.run(["systemctl", "is-active", "bender-converse"],
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip() == "active"
    except Exception:
        return False


def _set_service(action: str) -> bool:
    try:
        subprocess.run(["sudo", "systemctl", action, "bender-converse"],
                       check=True, timeout=60)
        return True
    except Exception as e:
        print(f"  ! could not {action} bender-converse: {e}")
        return False


def _record(seconds: float, path: str) -> np.ndarray:
    device = getattr(cfg, "input_device_name", "mic_shared")
    subprocess.run(
        ["arecord", "-D", device, "-f", "S16_LE", "-r", str(RATE),
         "-c", "1", "-d", str(int(round(seconds))), "-q", path],
        check=True)
    with wave.open(path) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def trim_to_voiced(pcm: np.ndarray, clip_s: float = CLIP_S) -> "np.ndarray | None":
    """Trim to the voiced span plus padding, then fit to exactly clip_s.

    Returns None when VAD finds no speech at all — a missed utterance must be
    dropped, not padded into a silent 'positive' that teaches the model the
    wake word sounds like nothing.
    """
    import webrtcvad
    vad = webrtcvad.Vad(2)
    n = int(RATE * FRAME_MS / 1000)
    voiced = [i for i in range(0, len(pcm) - n, n)
              if vad.is_speech(pcm[i:i + n].tobytes(), RATE)]
    if not voiced:
        return None

    pad = int(RATE * PAD_MS / 1000)
    start = max(0, voiced[0] - pad)
    end = min(len(pcm), voiced[-1] + n + pad)
    clip = pcm[start:end]

    want = int(RATE * clip_s)
    if len(clip) > want:                      # centre-crop the overspill
        off = (len(clip) - want) // 2
        clip = clip[off:off + want]
    elif len(clip) < want:                    # centre in a silent frame
        out = np.zeros(want, dtype=np.int16)
        off = (want - len(clip)) // 2
        out[off:off + len(clip)] = clip
        clip = out
    return clip


def _write(path: str, pcm: np.ndarray) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())


class _Scorer:
    """Scores clips with the currently deployed model, for a before-baseline."""

    def __init__(self):
        self.model = None
        try:
            from openwakeword.model import Model
            path = os.path.join(BASE_DIR, cfg.oww_model_path)
            if os.path.exists(path):
                self.model = Model(wakeword_model_paths=[path])
        except Exception as e:
            print(f"  (scoring unavailable: {e})")

    def peak(self, pcm: np.ndarray) -> float:
        if self.model is None:
            return -1.0
        best = 0.0
        for i in range(0, len(pcm) - 1280, 1280):
            pred = self.model.predict(pcm[i:i + 1280])
            best = max(best, max(pred.values()) if pred else 0.0)
        return best


def _log(entry: dict) -> None:
    os.makedirs(OUT_ROOT, exist_ok=True)
    with open(os.path.join(OUT_ROOT, "manifest.jsonl"), "a") as f:
        f.write(json.dumps(entry) + "\n")


def _prompt(msg: str) -> bool:
    """Return False if the user wants to stop."""
    try:
        reply = input(msg).strip().lower()
    except EOFError:
        return False
    return reply not in ("q", "quit", "stop")


def capture_prompted(args, items, mode: str) -> None:
    scorer = _Scorer()
    threshold = cfg.oww_threshold
    kept = fired = 0
    scratch = os.path.join(OUT_ROOT, ".scratch.wav")

    for label, hint in items:
        print(f"\n=== {label} — {hint} ===")
        for n in range(1, args.per_condition + 1):
            if not _prompt(f"  [{n}/{args.per_condition}] Enter to record "
                           f"(q to quit): "):
                print("\nStopping early.")
                return
            print("  recording...", end="", flush=True)
            raw = _record(RECORD_S, scratch)
            clip = trim_to_voiced(raw)
            if clip is None:
                print(" no speech detected — skipped, try again")
                continue

            peak = int(np.abs(clip).max())
            score = scorer.peak(clip)
            kept += 1
            if score >= threshold:
                fired += 1

            out = os.path.join(OUT_ROOT, mode, args.speaker,
                               f"{label}_{n:03d}.wav")
            _write(out, clip)
            _log({"path": os.path.relpath(out, BASE_DIR), "mode": mode,
                  "speaker": args.speaker, "condition": label,
                  "peak_level": peak, "current_model_score": round(score, 4),
                  "ts": time.strftime("%Y-%m-%dT%H:%M:%S")})

            flags = []
            if peak > 32000:
                flags.append("CLIPPING — move back or lower your voice")
            elif peak < 800:
                flags.append("very quiet — move closer")
            verdict = "would wake" if score >= threshold else "MISSED"
            print(f" saved  level={peak:5d}  score={score:.3f}  {verdict}"
                  + ("  <- " + "; ".join(flags) if flags else ""))

    if kept:
        print(f"\n{kept} clips. Current model would have woken on "
              f"{fired}/{kept} ({100 * fired / kept:.0f}%) at threshold "
              f"{threshold}. That is the number the retrain must beat.")


def capture_ambient(args) -> None:
    """Continuous household audio: the negative set that keeps FPs down."""
    print(f"Recording {args.minutes} minutes of background audio.")
    print("Talk, watch TV, cook — anything EXCEPT saying the wake word.")
    chunk_s = 60
    for i in range(args.minutes):
        out = os.path.join(OUT_ROOT, "ambient", f"{i:03d}.wav")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        print(f"  minute {i + 1}/{args.minutes}...", flush=True)
        _record(chunk_s, out)
        _log({"path": os.path.relpath(out, BASE_DIR), "mode": "ambient",
              "seconds": chunk_s, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")})
    print("Done.")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--speaker", help="who is speaking (one run per person)")
    ap.add_argument("--mode", default="positive",
                    choices=["positive", "hard_negative", "ambient"])
    ap.add_argument("--per-condition", type=int, default=10,
                    help="clips per condition / per phrase (default 10)")
    ap.add_argument("--minutes", type=int, default=20,
                    help="ambient mode only")
    ap.add_argument("--keep-service", action="store_true",
                    help="don't stop bender-converse (he may wake mid-capture)")
    args = ap.parse_args()

    if args.mode != "ambient" and not args.speaker:
        ap.error("--speaker is required unless --mode ambient")

    # Bender waking mid-capture would talk over the recordings, and playback
    # flips the WM8960 to 44100Hz. Stop him unless told otherwise.
    stopped = False
    if not args.keep_service and _service_active():
        print("Stopping bender-converse for the capture session...")
        stopped = _set_service("stop")
        if not stopped:
            print("  continuing anyway — he may wake mid-capture")

    try:
        if args.mode == "ambient":
            capture_ambient(args)
        elif args.mode == "hard_negative":
            capture_prompted(
                args, [(p.replace(" ", "_"), f'say: "{p}"')
                       for p in HARD_NEGATIVE_PHRASES], "hard_negative")
        else:
            capture_prompted(args, POSITIVE_CONDITIONS, "positive")
    finally:
        if stopped:
            print("\nRestarting bender-converse...")
            # NB: repeated stop/start cycles burn the unit's StartLimitBurst
            # (5 starts / 300s). If this fails, check `systemctl show
            # bender-converse -p Result` for start-limit-hit before suspecting
            # anything else, then reset-failed + start.
            _set_service("start")


if __name__ == "__main__":
    main()
