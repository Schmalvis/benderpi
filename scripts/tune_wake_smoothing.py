#!/usr/bin/env python3
"""Find the (threshold, N-of-M) setting that keeps recall without false wakes.

WHY
---
v0.3_r35 shipped on 2026-09-30 with 92% near-field recall and a measured
"0 false wakes per hour". It then woke four times in a day while the owner was
on work calls. The gate was not wrong about its own data -- it was computed on
3 minutes of held-out conversation, and 3 minutes cannot verify a rate of
roughly 0.4/hour. This tool exists so that claim is never made on a sample
that small again.

Two of the four false wakes scored 0.779 and 0.827, so RAISING THE THRESHOLD
CANNOT FIX IT: 0.35 or even 0.5 stops the quiet two and keeps the loud two.
The other lever is the wake loop's N-of-M smoothing (`oww_frames_required` of
`oww_window`): a passing burst of conversation may clear the line for two
frames where a real phrase sustains longer. That is what this sweeps.

HONESTY ABOUT THE DATA
----------------------
This uses EVERY minute of negative audio, held out or not, because 3 minutes
was the whole problem. That makes it a TUNING set, not a test set: whatever
setting wins here is fitted to this audio. It must then be confirmed on fresh
recordings (scripts/capture_background.py) before any claim is made.

Usage, on the device:
    venv/bin/python scripts/tune_wake_smoothing.py --model models/hey_bender_v0.3_r35.onnx
    venv/bin/python scripts/tune_wake_smoothing.py --model ... --extra data/wake_samples/calls
"""

import argparse
import glob
import json
import os
import sys
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import eval_wake_model as ev  # noqa: E402  (frame size, Scorer, engine choice)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPLIT_PATH = os.path.join(BASE_DIR, "data", "wake_samples", "split.json")
NEGATIVE_DIRS = ("ambient", "conversation")

THRESHOLDS = (0.05, 0.10, 0.20, 0.35, 0.50, 0.70, 0.85)
# (required, window). 2-of-4 is what shipped and what failed.
SMOOTHINGS = ((2, 4), (3, 4), (3, 6), (4, 6), (4, 8), (5, 8), (6, 10))


def _read(path: str) -> np.ndarray:
    with wave.open(path) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def _fires(scores: np.ndarray, threshold: float, required: int, window: int) -> int:
    """Distinct wake events, consuming the window on each one -- a single noisy
    second must not count as thirty."""
    n, i = 0, 0
    end = max(1, len(scores) - window + 1)
    while i < end:
        if int((scores[i:i + window] >= threshold).sum()) >= required:
            n += 1
            i += window
        else:
            i += 1
    return n


def _would_fire(scores: np.ndarray, threshold: float, required: int,
                window: int) -> bool:
    for i in range(max(1, len(scores) - window + 1)):
        if int((scores[i:i + window] >= threshold).sum()) >= required:
            return True
    return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--engine", choices=["openwakeword", "livekit"])
    ap.add_argument("--extra", action="append", default=[],
                    help="extra directory of negative audio (e.g. recorded calls)")
    ap.add_argument("--json", help="write the full grid here")
    args = ap.parse_args()

    if not os.path.exists(SPLIT_PATH):
        raise SystemExit(f"{SPLIT_PATH} missing — run split_wake_samples.py")
    split = json.load(open(SPLIT_PATH))
    scorer = ev.Scorer(args.model, args.engine)
    print(f"model {os.path.basename(scorer.path)} via the {scorer.engine} engine\n")

    # --- negatives: every minute of them, plus anything extra ---
    neg_files = []
    for d in NEGATIVE_DIRS:
        neg_files += sorted(glob.glob(
            os.path.join(BASE_DIR, "data", "wake_samples", d, "*.wav")))
    for d in args.extra:
        root = d if os.path.isabs(d) else os.path.join(BASE_DIR, d)
        neg_files += sorted(glob.glob(os.path.join(root, "**", "*.wav"),
                                      recursive=True))
    if not neg_files:
        raise SystemExit("no negative audio found")

    neg_scores, neg_seconds = [], 0.0
    for f in neg_files:
        pcm = _read(f)
        neg_seconds += len(pcm) / ev.RATE
        neg_scores.append(scorer.frame_scores(pcm))
        print(f"  scored {os.path.basename(f)} ({len(pcm)/ev.RATE:.0f}s)", flush=True)
    hours = neg_seconds / 3600
    print(f"\nnegatives: {len(neg_files)} files, {neg_seconds/60:.1f} minutes")

    # --- positives: held out only, so recall stays honest ---
    pos_scores = []
    for rel in split["positive"]["holdout"]:
        p = os.path.join(BASE_DIR, rel)
        if os.path.exists(p):
            pos_scores.append((ev._group(rel), scorer.frame_scores(_read(p))))
    near = [(g, s) for g, s in pos_scores if g in ev.NEAR_FIELD_CONDITIONS]
    print(f"held-out positives: {len(pos_scores)} ({len(near)} near-field)\n")

    rows = []
    print(f"{'thr':>5} {'N-of-M':>7} {'near-field':>11} {'all pos':>8} "
          f"{'false wakes':>12} {'per hour':>9}")
    for req, win in SMOOTHINGS:
        for t in THRESHOLDS:
            nf = sum(1 for _, s in near if _would_fire(s, t, req, win))
            allp = sum(1 for _, s in pos_scores if _would_fire(s, t, req, win))
            fw = sum(_fires(s, t, req, win) for s in neg_scores)
            rows.append({"threshold": t, "required": req, "window": win,
                         "near_recall": nf / max(1, len(near)),
                         "all_recall": allp / max(1, len(pos_scores)),
                         "false_wakes": fw,
                         "per_hour": fw / hours if hours else 0.0})
            print(f"{t:5.2f} {f'{req}-of-{win}':>7} "
                  f"{nf:4d}/{len(near):<2d} {100*nf/max(1,len(near)):4.0f}% "
                  f"{allp:3d}/{len(pos_scores):<2d} "
                  f"{fw:8d}     {fw/hours if hours else 0:7.2f}")

    clean = [r for r in rows if r["false_wakes"] == 0]
    print("\n--- settings with ZERO false wakes on this audio, best recall first ---")
    if not clean:
        print("  none. Every setting fires at least once on these 30 minutes.")
    for r in sorted(clean, key=lambda r: -r["near_recall"])[:6]:
        print(f"  threshold {r['threshold']:.2f}, {r['required']}-of-{r['window']}: "
              f"near-field {100*r['near_recall']:.0f}%, "
              f"overall {100*r['all_recall']:.0f}%")
    print("\nThis audio was used to CHOOSE the setting, so these numbers are "
          "fitted to it.\nConfirm the winner on fresh recordings before "
          "claiming a false-wake rate.")

    if args.json:
        with open(args.json, "w") as f:
            json.dump({"model": os.path.basename(scorer.path),
                       "negative_minutes": neg_seconds / 60, "grid": rows}, f, indent=2)
        print(f"\nWrote {args.json}")


if __name__ == "__main__":
    main()
