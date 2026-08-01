#!/usr/bin/env python3
"""Score live or recorded audio against the configured wake-word model.

Answers the question the journal can't: *why* isn't the wake word firing?
`wake_converse.py` logs a peak score per 60s, which tells you something is
wrong but not whether the fault is the mic, the audio path, or the model.

This reproduces the wake loop's scoring exactly — same model construction
(`wakeword_model_paths`), same 1280-sample framing, same
`max(prediction.values())` reduction — so its numbers are directly comparable
to the "Wake idle: peak score ..." lines in journald.

It records from `input_device_name` (a dsnoop device by default), so it runs
**alongside** a live `bender-converse` without stopping it or stealing the mic.

    # say the wake word a few times during the window
    venv/bin/python scripts/wake_score.py --record 20

    # score a file instead
    venv/bin/python scripts/wake_score.py --wav /tmp/sample.wav

    # sanity-check the harness itself against a synthetic positive
    venv/bin/python scripts/wake_score.py --synthetic

Interpreting the result, using the 2026-08-01 measurements as reference:
  ~0.97 synthetic, ~0.25 your voice  -> model doesn't generalise off its
                                        TTS-trained positives; lower the
                                        threshold or retrain on real voices
  high score but the loop never fires -> the loop's audio path is at fault,
                                        not the model
  peak level near zero                -> nothing reached the mic at all
"""
import argparse
import os
import subprocess
import sys
import tempfile
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import cfg  # noqa: E402

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRAME = 1280          # must match wake_converse.OWW_FRAME_SIZE
RATE = 16000
FRAME_S = FRAME / RATE
SPEECH_LEVEL = 1500   # peak sample above which a frame plausibly holds speech


def _read_wav(path):
    with wave.open(path) as w:
        if w.getframerate() != RATE:
            raise SystemExit(
                f"{path} is {w.getframerate()}Hz; the wake model needs {RATE}Hz")
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def _record(seconds, path):
    device = getattr(cfg, "input_device_name", "mic_shared")
    print(f"Recording {seconds}s from '{device}' — say the wake word now...",
          flush=True)
    subprocess.run(
        ["arecord", "-D", device, "-f", "S16_LE", "-r", str(RATE),
         "-c", "1", "-d", str(seconds), "-q", path],
        check=True)
    return _read_wav(path)


def _synthetic(path):
    """A Piper-rendered wake word: a known-good positive for the model."""
    piper = os.path.join(BASE_DIR, "piper", "piper")
    model = os.path.join(BASE_DIR, "models", "bender.onnx")
    if not (os.path.exists(piper) and os.path.exists(model)):
        raise SystemExit("piper binary or models/bender.onnx missing")
    subprocess.run([piper, "--model", model, "--output_file", path],
                   input=b"hey bender", check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    from scipy.signal import resample_poly
    with wave.open(path) as w:
        sr = w.getframerate()
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    pcm = resample_poly(pcm.astype(np.float64), RATE, sr).astype(np.int16)
    # openWakeWord needs ~1.5s of context; pad so the word isn't at the edge.
    pad = np.zeros(RATE * 2, dtype=np.int16)
    return np.concatenate([pad, pcm, pad])


def score(pcm):
    """Frame scores + peak levels, mirroring wake_converse's inner loop."""
    from openwakeword.model import Model
    path = os.path.join(BASE_DIR, cfg.oww_model_path)
    if not os.path.exists(path):
        raise SystemExit(f"wake model not found: {path}")
    model = Model(wakeword_model_paths=[path])

    scores, levels = [], []
    for i in range(0, len(pcm) - FRAME, FRAME):
        chunk = pcm[i:i + FRAME]
        pred = model.predict(chunk)
        scores.append(max(pred.values()) if pred else 0.0)
        levels.append(int(np.abs(chunk).max()))
    return np.array(scores), np.array(levels)


def would_fire(scores, threshold):
    """Replicate the N-of-M smoothing gate in wake_converse."""
    window = max(1, int(getattr(cfg, "oww_window", 1)))
    required = max(1, min(int(getattr(cfg, "oww_frames_required", 1)), window))
    for i in range(max(0, len(scores) - window + 1)):
        if int((scores[i:i + window] >= threshold).sum()) >= required:
            return True, required, window
    return False, required, window


def report(scores, levels):
    if not len(scores):
        raise SystemExit("no audio to score")
    threshold = cfg.oww_threshold
    fires, required, window = would_fire(scores, threshold)

    print(f"\nframes={len(scores)}  duration={len(scores) * FRAME_S:.1f}s")
    print(f"PEAK SCORE = {scores.max():.4f}   (threshold {threshold})")
    print(f"peak level = {levels.max()}   "
          f"clipped frames = {int((levels > 32000).sum())}")
    print(f"WOULD FIRE = {fires}   ({required}-of-{window} frames over threshold)")

    speech = np.where(levels > SPEECH_LEVEL)[0]
    if len(speech):
        print(f"\nframes with speech-level audio: {len(speech)}")
        print(f"  peak score on those frames: {scores[speech].max():.4f}")
    else:
        print(f"\nNo frame exceeded level {SPEECH_LEVEL} — nothing loud enough "
              f"to be speech reached the mic. The scores below mean nothing "
              f"about the model; re-run and speak during the window.")

    print("\nwould-fire at other thresholds:")
    for t in (0.35, 0.20, 0.15, 0.10, 0.05):
        hit, _, _ = would_fire(scores, t)
        print(f"  {t:.2f}: {'FIRE' if hit else 'miss'}")

    top = np.argsort(scores)[-5:][::-1]
    print("\ntop frames (t_sec, score, peak_level):")
    for i in sorted(top):
        print(f"  {i * FRAME_S:6.2f}s  {scores[i]:.4f}  {levels[i]}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--record", type=int, metavar="SECONDS",
                     help="record from the configured mic and score it")
    src.add_argument("--wav", metavar="PATH", help="score an existing 16kHz WAV")
    src.add_argument("--synthetic", action="store_true",
                     help="score a Piper-rendered wake word (harness check)")
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        scratch = os.path.join(tmp, "wake_score.wav")
        if args.record:
            pcm = _record(args.record, scratch)
        elif args.synthetic:
            pcm = _synthetic(scratch)
        else:
            pcm = _read_wav(args.wav)
        report(*score(pcm))


if __name__ == "__main__":
    main()
