#!/usr/bin/env python3
"""Score a wake-word model against the HELD-OUT real-voice clips.

WHY THIS EXISTS
---------------
v0.1 scores 0.97 on a synthetic "hey bender" and woke on 6 of 100 real ones.
Any metric computed on data the model trained on would have looked fine
throughout. So the only number that means anything is recall on clips the
training run never saw, measured through the device's own microphone path --
which is why this runs on BenderPi and not on a dev clone.

WHAT IT MEASURES
----------------
For each threshold (default 0.10 / 0.35 / 0.50), using the live wake loop's
own N-of-M smoothing rather than a bare peak score, because a single hot frame
does not wake the device:

  recall        -- held-out positives that would wake it, overall and per condition
  false wakes   -- held-out hard negatives that would wake it
  per hour      -- wake events across the held-out ambient and conversation audio
  watch         -- "hey bender's" clips, reported and never gated: the phrase is
                   in there, so a wake is acceptable behaviour, not a failure

It also prints the synthetic control (~0.97 expected) and the capture
zero-fraction, because a corrupt XVF3800 stream reads 0.001 on everything and
is indistinguishable from a failed retrain if you are not looking for it.

Usage:
    venv/bin/python scripts/eval_wake_model.py
    venv/bin/python scripts/eval_wake_model.py --model models/hey_bender_v0.2_r20.onnx
    venv/bin/python scripts/eval_wake_model.py --model models/a.onnx --compare models/hey_bender_v0.1.onnx
    venv/bin/python scripts/eval_wake_model.py --model models/a.onnx --json results.json

Ship gates (docs/superpowers/plans/2026-09-24-wake-word-retrain-v0.2.md), all
at threshold 0.35 and all on held-out data:
    recall >= 80%, recall on normal-speech conditions >= 75%,
    hard-negative false wakes <= 10%, ambient false wakes = 0/hour.
"""

import argparse
import collections
import json
import os
import subprocess
import sys
import tempfile
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import cfg  # noqa: E402

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPLIT_PATH = os.path.join(BASE_DIR, "data", "wake_samples", "split.json")
FRAME = 1280          # must match wake_converse.OWW_FRAME_SIZE
RATE = 16000
FRAME_S = FRAME / RATE
DEFAULT_THRESHOLDS = (0.10, 0.35, 0.50)
SHIP_THRESHOLD = 0.35

# Conditions where someone is simply talking to the device in an ordinary
# voice. v0.1 woke on 0 of these 60 clips while scoring 0.868 on a drawn-out
# "heeey benderrr", so they are gated separately: a model that only works when
# you slow down has not been fixed.
NORMAL_CONDITIONS = ("close_normal", "mid_normal", "far_normal", "mid_quiet",
                     "mid_loud", "mid_fast")

SHIP_GATES = {
    "recall": 0.80,
    "recall_normal": 0.75,
    "hard_negative_rate": 0.10,
    "ambient_per_hour": 0.0,
}


def _read_wav(path: str) -> np.ndarray:
    with wave.open(path) as w:
        if w.getframerate() != RATE:
            raise SystemExit(f"{path} is {w.getframerate()}Hz, need {RATE}Hz")
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def _group(path: str) -> str:
    return os.path.basename(path).rsplit("_", 1)[0]


class Scorer:
    """One loaded model, scored exactly as the wake loop scores frames."""

    def __init__(self, model_path: str):
        from openwakeword.model import Model
        full = model_path if os.path.isabs(model_path) \
            else os.path.join(BASE_DIR, model_path)
        if not os.path.exists(full):
            raise SystemExit(f"model not found: {full}")
        self.path = full
        self.model = Model(wakeword_model_paths=[full])

    def frame_scores(self, pcm: np.ndarray) -> np.ndarray:
        out = []
        for i in range(0, len(pcm) - FRAME + 1, FRAME):
            pred = self.model.predict(pcm[i:i + FRAME])
            out.append(float(max(pred.values())) if pred else 0.0)
        return np.array(out) if out else np.zeros(0)


def _smoothing() -> "tuple[int, int]":
    window = max(1, int(getattr(cfg, "oww_window", 1)))
    required = max(1, min(int(getattr(cfg, "oww_frames_required", 1)), window))
    return required, window


def would_fire(scores: np.ndarray, threshold: float) -> bool:
    """The live gate: N of M consecutive frames over threshold.

    Peak score alone overstates recall -- the wake loop needs a second frame
    over the line, and that one sits materially lower than the best frame.
    """
    required, window = _smoothing()
    # `max(1, ...)`: a clip shorter than the window still has to be judged.
    # With range(len - window + 1) a 3-frame score array produced an empty
    # range and could never fire, silently scoring short clips as misses.
    for i in range(max(1, len(scores) - window + 1)):
        if int((scores[i:i + window] >= threshold).sum()) >= required:
            return True
    return False


def count_fires(scores: np.ndarray, threshold: float) -> int:
    """Distinct wake events in a long recording.

    One noisy second must not count as thirty false wakes, so a fire consumes
    its window and scanning resumes after it -- the same way a real session
    would start, run, and return to listening.
    """
    required, window = _smoothing()
    fires, i = 0, 0
    end = max(1, len(scores) - window + 1)   # see would_fire: judge short runs too
    while i < end:
        if int((scores[i:i + window] >= threshold).sum()) >= required:
            fires += 1
            i += window
        else:
            i += 1
    return fires


def _clip_results(scorer: Scorer, paths: "list[str]", thresholds) -> dict:
    """Per-clip peak score and per-threshold fire decisions."""
    rows = []
    for rel in paths:
        full = os.path.join(BASE_DIR, rel)
        if not os.path.exists(full):
            continue
        s = scorer.frame_scores(_read_wav(full))
        rows.append({
            "path": rel,
            "group": _group(rel),
            "peak": float(s.max()) if len(s) else 0.0,
            "fires": {t: would_fire(s, t) for t in thresholds},
        })
    return rows


def _continuous_results(scorer: Scorer, paths: "list[str]", thresholds) -> dict:
    fires = {t: 0 for t in thresholds}
    peak, seconds = 0.0, 0.0
    for rel in paths:
        full = os.path.join(BASE_DIR, rel)
        if not os.path.exists(full):
            continue
        pcm = _read_wav(full)
        seconds += len(pcm) / RATE
        s = scorer.frame_scores(pcm)
        if len(s):
            peak = max(peak, float(s.max()))
        for t in thresholds:
            fires[t] += count_fires(s, t)
    return {"seconds": seconds, "peak": peak, "fires": fires}


def evaluate(model_path: str, split: dict, thresholds=DEFAULT_THRESHOLDS,
             synthetic_pcm=None) -> dict:
    scorer = Scorer(model_path)
    res = {"model": os.path.basename(scorer.path), "thresholds": list(thresholds)}
    res["positive"] = _clip_results(scorer, split["positive"]["holdout"], thresholds)
    res["hard_negative"] = _clip_results(
        scorer, split["hard_negative"]["holdout"], thresholds)
    res["watch"] = _clip_results(
        scorer, split["hard_negative"].get("watch", []), thresholds)
    res["ambient"] = _continuous_results(
        scorer, split["ambient"]["holdout"], thresholds)
    res["conversation"] = _continuous_results(
        scorer, split.get("conversation", {}).get("holdout", []), thresholds)
    res["synthetic"] = _synthetic_score(scorer, synthetic_pcm)
    return res


SYNTHETIC_CONTROL = os.path.join(BASE_DIR, "data", "wake_samples",
                                 "synthetic_control.wav")


def render_synthetic(reference_model: "str | None" = None) -> "np.ndarray | None":
    """The harness self-check: a fixed Piper "hey bender", padded with silence.

    It has to be a CACHED FILE, not a fresh render. Piper is stochastic --
    measured on-device 2026-09-28, five renders of the same text came out
    1.07-1.12s long and v0.1 scored them 0.966, 0.957, 0.071, 0.964, 0.071.
    Two of five fall off a cliff, so a per-run render made the control swing
    between 0.97 and 0.13 and look like a broken export. (That the wake model
    is that brittle on its OWN training distribution is itself a finding.)

    On first use the file is created: render, score with the reference model
    (the deployed one by default), keep the first render it recognises. The
    control exists to prove the harness and the export work, so it must be
    audio a known-good model actually wakes on.
    """
    if os.path.exists(SYNTHETIC_CONTROL):
        return _read_wav(SYNTHETIC_CONTROL)

    piper = os.path.join(BASE_DIR, "piper", "piper")
    model = os.path.join(BASE_DIR, "models", "bender.onnx")
    if not (os.path.exists(piper) and os.path.exists(model)):
        return None
    try:
        from scipy.signal import resample_poly
        ref = Scorer(reference_model or cfg.oww_model_path) if reference_model \
            or os.path.exists(os.path.join(BASE_DIR, cfg.oww_model_path)) else None
        best, best_score = None, -1.0
        for attempt in range(5):
            raw = tempfile.mktemp(suffix=".wav")
            try:
                subprocess.run([piper, "--model", model, "--output_file", raw],
                               input=b"hey bender", check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                with wave.open(raw) as w:
                    sr = w.getframerate()
                    pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
            finally:
                try:
                    os.unlink(raw)
                except OSError:
                    pass
            pcm = resample_poly(pcm.astype(np.float64), RATE, sr).astype(np.int16)
            pad = np.zeros(RATE * 2, dtype=np.int16)
            padded = np.concatenate([pad, pcm, pad])
            score = float(ref.frame_scores(padded).max()) if ref else 1.0
            if score > best_score:
                best, best_score = padded, score
            if score >= 0.5:
                break
        os.makedirs(os.path.dirname(SYNTHETIC_CONTROL), exist_ok=True)
        with wave.open(SYNTHETIC_CONTROL, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(best.tobytes())
        print(f"Cached the synthetic control ({best_score:.3f} on the reference "
              f"model) at {SYNTHETIC_CONTROL}")
        if best_score < 0.5:
            print("  WARNING: no render scored above 0.5. Treat the control "
                  "column as unreliable until this file is replaced.")
        return best
    except Exception as exc:
        print(f"  (synthetic control unavailable: {exc})")
        return None


def _synthetic_score(scorer: Scorer, pcm) -> float:
    """Score the shared synthetic render. Expect ~0.97 for a sane model: a
    candidate that collapses here has an export or harness problem, which is a
    different bug from one that simply does not generalise."""
    if pcm is None:
        return -1.0
    s = scorer.frame_scores(pcm)
    return float(s.max()) if len(s) else 0.0


def _rate(rows, t) -> "tuple[int, int]":
    return sum(1 for r in rows if r["fires"][t]), len(rows)


def gate_results(res: dict, t: float = SHIP_THRESHOLD) -> dict:
    pos = res["positive"]
    fired, n = _rate(pos, t)
    normal = [r for r in pos if r["group"] in NORMAL_CONDITIONS]
    nf, nn = _rate(normal, t)
    hf, hn = _rate(res["hard_negative"], t)
    amb = res["ambient"]
    per_hour = (amb["fires"][t] / (amb["seconds"] / 3600)) if amb["seconds"] else 0.0
    got = {
        "recall": fired / n if n else 0.0,
        "recall_normal": nf / nn if nn else 0.0,
        "hard_negative_rate": hf / hn if hn else 0.0,
        "ambient_per_hour": per_hour,
    }
    passed = {
        "recall": got["recall"] >= SHIP_GATES["recall"],
        "recall_normal": got["recall_normal"] >= SHIP_GATES["recall_normal"],
        "hard_negative_rate": got["hard_negative_rate"] <= SHIP_GATES["hard_negative_rate"],
        "ambient_per_hour": got["ambient_per_hour"] <= SHIP_GATES["ambient_per_hour"],
    }
    return {"threshold": t, "values": got, "passed": passed,
            "ship": all(passed.values())}


def _mic_zero_fraction(seconds: int = 3) -> float:
    """Exact-zero sample fraction of a live capture.

    The XVF3800 cold-boot fault delivers 4 real samples in every 48 and scores
    0.001 on everything, which looks exactly like a model that does not work.
    Check the mic before believing any of the numbers below.
    """
    device = getattr(cfg, "input_device_name", "mic_shared")
    path = tempfile.mktemp(suffix=".wav")
    try:
        subprocess.run(["arecord", "-D", device, "-f", "S16_LE", "-r", str(RATE),
                        "-c", "1", "-d", str(seconds), "-q", path],
                       check=True, capture_output=True)
        pcm = _read_wav(path)
        return float(np.mean(pcm == 0)) if len(pcm) else 1.0
    except Exception:
        return -1.0
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def print_report(results: "list[dict]", thresholds) -> None:
    names = [r["model"] for r in results]
    w = max(22, max(len(n) for n in names) + 2)

    def row(label, values):
        print(f"  {label:34s}" + "".join(f"{v:>{w}}" for v in values))

    for t in thresholds:
        print(f"\n{'=' * (36 + w * len(results))}")
        print(f"  THRESHOLD {t:.2f}" + "".join(f"{n:>{w}}" for n in names))
        print(f"{'=' * (36 + w * len(results))}")

        vals = []
        for r in results:
            f, n = _rate(r["positive"], t)
            vals.append(f"{f}/{n}  {100 * f / n if n else 0:.0f}%")
        row("recall (held-out positives)", vals)

        vals = []
        for r in results:
            normal = [x for x in r["positive"] if x["group"] in NORMAL_CONDITIONS]
            f, n = _rate(normal, t)
            vals.append(f"{f}/{n}  {100 * f / n if n else 0:.0f}%")
        row("  of which normal speech", vals)

        groups = sorted({x["group"] for x in results[0]["positive"]})
        for g in groups:
            vals = []
            for r in results:
                sub = [x for x in r["positive"] if x["group"] == g]
                f, n = _rate(sub, t)
                vals.append(f"{f}/{n}")
            row(f"    {g}", vals)

        vals = []
        for r in results:
            f, n = _rate(r["hard_negative"], t)
            vals.append(f"{f}/{n}  {100 * f / n if n else 0:.0f}%")
        row("false wakes (hard negatives)", vals)

        for key, label in (("ambient", "ambient false wakes"),
                           ("conversation", "conversation false wakes")):
            vals = []
            for r in results:
                d = r[key]
                hrs = d["seconds"] / 3600
                rate = d["fires"][t] / hrs if hrs else 0.0
                vals.append(f"{d['fires'][t]}  ({rate:.1f}/h)")
            row(label, vals)

        vals = []
        for r in results:
            f, n = _rate(r["watch"], t)
            vals.append(f"{f}/{n}" + ("" if n else "  n/a"))
        row("watch: \"hey bender's\" (not gated)", vals)

    print(f"\n{'-' * 60}")
    row("synthetic control (expect ~0.97)",
        [f"{r['synthetic']:.3f}" if r["synthetic"] >= 0 else "n/a" for r in results])

    print(f"\n{'=' * 60}")
    print(f"  SHIP GATES @ {SHIP_THRESHOLD:.2f} (held-out data only)")
    print(f"{'=' * 60}")
    labels = {"recall": "recall >= 80%",
              "recall_normal": "normal-speech recall >= 75%",
              "hard_negative_rate": "hard-negative false wakes <= 10%",
              "ambient_per_hour": "ambient false wakes = 0/h"}
    for r in results:
        g = gate_results(r)
        print(f"\n  {r['model']}")
        for k, label in labels.items():
            v = g["values"][k]
            shown = f"{100 * v:.0f}%" if k != "ambient_per_hour" else f"{v:.1f}/h"
            print(f"    [{'PASS' if g['passed'][k] else 'FAIL'}] {label:36s} {shown}")
        print(f"    => {'SHIP' if g['ship'] else 'DO NOT SHIP'}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=cfg.oww_model_path,
                    help="model to evaluate (default: the deployed one)")
    ap.add_argument("--compare", action="append", default=[],
                    help="another model to score side by side; repeatable")
    ap.add_argument("--thresholds", default=",".join(str(t) for t in DEFAULT_THRESHOLDS))
    ap.add_argument("--json", help="write the full results to this path")
    ap.add_argument("--skip-mic-check", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(SPLIT_PATH):
        raise SystemExit(
            f"{SPLIT_PATH} missing. Run scripts/split_wake_samples.py first — "
            "without a frozen split there is no held-out set to score.")
    split = json.load(open(SPLIT_PATH))
    thresholds = tuple(float(t) for t in args.thresholds.split(","))

    if not args.skip_mic_check:
        z = _mic_zero_fraction()
        if z < 0:
            print("mic check: could not record (is bender-converse holding the "
                  "device? that is fine, the clips below are files)")
        elif z >= float(getattr(cfg, "mic_zero_frac_max", 0.5)):
            print(f"\n*** MIC STREAM IS CORRUPT ({z:.0%} exact-zero samples). ***")
            print("The XVF3800 cold-boot fault makes every score read ~0.001, "
                  "which looks exactly like a broken model. Recover first:")
            print("  sudo systemctl restart bender-converse   # sends REBOOT")
            raise SystemExit(2)
        else:
            print(f"mic check: {z:.1%} exact-zero samples — stream is healthy")

    synthetic = render_synthetic()
    results = []
    for m in [args.model] + args.compare:
        print(f"scoring {m} ...", flush=True)
        results.append(evaluate(m, split, thresholds, synthetic))

    print_report(results, thresholds)

    if args.json:
        with open(args.json, "w") as f:
            json.dump({"results": results,
                       "gates": [gate_results(r) for r in results]}, f, indent=2)
            f.write("\n")
        print(f"\nWrote {args.json}")


if __name__ == "__main__":
    main()
