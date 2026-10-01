#!/usr/bin/env python3
"""Record long stretches of real background audio, unattended, while Bender
keeps running. Scores each minute against the live wake model as it goes.

WHY
---
v0.3_r35 shipped on 2026-09-30 claiming "0 false wakes per hour". It woke four
times the next day while the owner was on work calls. The claim was not a lie
about its data -- it was computed on THREE MINUTES of held-out conversation,
and three minutes cannot measure a rate of roughly 0.4/hour. Two of the four
wakes scored 0.779 and 0.827, so no threshold would have stopped them.

The missing ingredient is hours of the audio that actually causes the problem:
one person talking continuously, at a desk, near the device. This records it.

WHAT MAKES IT DIFFERENT FROM capture_wake_samples.py --mode conversation
------------------------------------------------------------------------
* It does NOT stop bender-converse. `mic_shared` is an ALSA dsnoop device, so
  several readers can share the capture stream -- the wake loop reads it at the
  same time. That matters here: this has to record the device's NORMAL
  operating condition, including it waking up by mistake.
* It runs for hours, not minutes, and survives being left alone: one file per
  chunk, written atomically, so a power cut or a Ctrl-C costs one chunk.
* It scores every chunk against the live model and writes a `triggers.jsonl`
  naming the timestamp and score of anything that would have woken the device.
  Those are labelled hard negatives, for free, from real life.

Usage, on the device (leave it running during calls):
    venv/bin/python scripts/capture_background.py --label work_calls --hours 3

    # just score what is already recorded, no new audio
    venv/bin/python scripts/capture_background.py --label work_calls --score-only

Then:
    venv/bin/python scripts/tune_wake_smoothing.py \
        --model models/hey_bender_v0.3_r35.onnx \
        --extra data/wake_samples/background/work_calls

PRIVACY: this records whatever is said in the room, including the other side of
a call if it comes out of a speaker. It writes to data/wake_samples/ which is
gitignored and excluded from the dataset upload. Delete a chunk you would
rather not keep -- the scores in triggers.jsonl stay valid without it.
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import cfg  # noqa: E402

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_ROOT = os.path.join(BASE_DIR, "data", "wake_samples", "background")
RATE = 16000
FRAME = 1280                 # must match wake_converse.OWW_FRAME_SIZE
DEFAULT_CHUNK_S = 300        # 5 minutes: small enough to lose little, few files

_stop = False


def _on_signal(_sig, _frm):
    global _stop
    _stop = True
    print("\n  stopping after this chunk (Ctrl-C again to abandon it)", flush=True)


def _record(seconds: int, path: str) -> bool:
    """Record one chunk to a .part file, then rename. An interrupted chunk
    leaves nothing: a partial file with a full-length header would quietly
    overstate how much audio exists (that bug bit the conversation set)."""
    device = getattr(cfg, "input_device_name", "mic_shared")
    part = path + ".part"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        subprocess.run(["arecord", "-D", device, "-f", "S16_LE", "-r", str(RATE),
                        "-c", "1", "-d", str(seconds), "-q", part], check=True)
    except (KeyboardInterrupt, subprocess.CalledProcessError):
        try:
            os.unlink(part)
        except OSError:
            pass
        return False
    os.replace(part, path)
    return True


def _read(path: str) -> np.ndarray:
    with wave.open(path) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


class _Scorer:
    """The live model, scored exactly as the wake loop scores it."""

    def __init__(self, model_path: str):
        self.required = max(1, int(getattr(cfg, "oww_frames_required", 2)))
        self.window = max(self.required, int(getattr(cfg, "oww_window", 4)))
        self.threshold = float(getattr(cfg, "oww_threshold", 0.1))
        self.name = os.path.basename(model_path)
        from openwakeword.model import Model
        full = os.path.join(BASE_DIR, model_path)
        self.model = Model(wakeword_model_paths=[full])

    def scan(self, pcm: np.ndarray) -> "tuple[list[dict], float]":
        """Wake events in this chunk, and the peak score."""
        scores = []
        for i in range(0, len(pcm) - FRAME + 1, FRAME):
            p = self.model.predict(pcm[i:i + FRAME])
            scores.append(float(max(p.values())) if p else 0.0)
        s = np.array(scores) if scores else np.zeros(0)
        events, i = [], 0
        end = max(1, len(s) - self.window + 1)
        while i < end:
            win = s[i:i + self.window]
            if int((win >= self.threshold).sum()) >= self.required:
                # at_s is the START of the triggering window, so seeking
                # there in the WAV lands just before the sound.
                events.append({"at_s": round(i * FRAME / RATE, 1),
                               "score": round(float(win.max()), 4)})
                i += self.window
            else:
                i += 1
        return events, (float(s.max()) if len(s) else 0.0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True,
                    help="what this recording is, e.g. work_calls, tv_evening")
    ap.add_argument("--hours", type=float, default=2.0)
    ap.add_argument("--chunk-seconds", type=int, default=DEFAULT_CHUNK_S)
    ap.add_argument("--score-only", action="store_true",
                    help="re-score existing chunks, record nothing")
    ap.add_argument("--no-score", action="store_true",
                    help="record only; score later with --score-only")
    args = ap.parse_args()

    out_dir = os.path.join(OUT_ROOT, args.label)
    os.makedirs(out_dir, exist_ok=True)
    trig_path = os.path.join(out_dir, "triggers.jsonl")

    scorer = None
    if not args.no_score:
        scorer = _Scorer(cfg.oww_model_path)
        print(f"scoring against {scorer.name} at threshold {scorer.threshold} "
              f"({scorer.required}-of-{scorer.window}) — the live settings")

    if args.score_only:
        files = sorted(f for f in os.listdir(out_dir) if f.endswith(".wav"))
        if not files:
            raise SystemExit(f"nothing recorded under {out_dir}")
        total_s, total_events = 0.0, 0
        with open(trig_path, "w") as tf:
            for name in files:
                pcm = _read(os.path.join(out_dir, name))
                total_s += len(pcm) / RATE
                events, peak = scorer.scan(pcm)
                total_events += len(events)
                for e in events:
                    tf.write(json.dumps({"file": name, **e}) + "\n")
                print(f"  {name}: {len(pcm)/60/RATE:.1f} min, peak {peak:.3f}, "
                      f"{len(events)} would-be wakes")
        hours = total_s / 3600
        print(f"\n{total_events} false wakes over {total_s/60:.1f} minutes "
              f"= {total_events/hours if hours else 0:.2f} per hour")
        print(f"Details: {trig_path}")
        return

    existing = len([f for f in os.listdir(out_dir) if f.endswith(".wav")])
    chunks = max(1, int(round(args.hours * 3600 / args.chunk_seconds)))
    print(f"Recording {chunks} chunks of {args.chunk_seconds}s "
          f"(~{args.hours:.1f}h) into {out_dir}")
    print("bender-converse keeps running — mic_shared is a dsnoop device, so "
          "this records the device's NORMAL condition, false wakes included.")
    print("Talk, take your calls, ignore this. Ctrl-C is safe between chunks.\n")

    signal.signal(signal.SIGINT, _on_signal)
    done, events_total, seconds_total = 0, 0, 0.0
    tf = open(trig_path, "a")
    for k in range(existing, existing + chunks):
        if _stop:
            break
        path = os.path.join(out_dir, f"{k:03d}.wav")
        print(f"  chunk {done + 1}/{chunks} -> {os.path.basename(path)} "
              f"({time.strftime('%H:%M:%S')})", flush=True)
        if not _record(args.chunk_seconds, path):
            print("  chunk abandoned")
            break
        done += 1
        pcm = _read(path)
        seconds_total += len(pcm) / RATE
        if scorer is not None:
            events, peak = scorer.scan(pcm)
            events_total += len(events)
            for e in events:
                tf.write(json.dumps({"file": os.path.basename(path), **e}) + "\n")
            tf.flush()
            print(f"      peak {peak:.3f}, {len(events)} would-be wakes", flush=True)
    tf.close()

    hours = seconds_total / 3600
    print(f"\nRecorded {done} chunks, {seconds_total/60:.1f} minutes.")
    if scorer is not None:
        print(f"{events_total} false wakes = "
              f"{events_total/hours if hours else 0:.2f} per hour "
              f"({scorer.name} at {scorer.threshold}, "
              f"{scorer.required}-of-{scorer.window})")
    print("\nNow test settings against it:")
    print(f"  venv/bin/python scripts/tune_wake_smoothing.py \\")
    print(f"      --model models/hey_bender_v0.3_r35.onnx \\")
    print(f"      --extra data/wake_samples/background/{args.label}")


if __name__ == "__main__":
    main()
