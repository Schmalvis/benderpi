#!/usr/bin/env python3
"""Fix the train/held-out split of the captured wake-word samples, once.

WHY A FILE AND NOT A FLAG
-------------------------
The held-out clips are the entire experiment: a model scored on clips it
trained on says nothing about the synthetic-to-real gap that made v0.1 miss
94 of 100 real utterances. So the division has to be identical for every
training run, every evaluation, and every later comparison -- including runs
months apart. It is therefore written once to ``data/wake_samples/split.json``
and read from there, never recomputed on the fly.

RULES
-----
* Positives: 2 of every 10 clips per condition are held out, so all ten
  recording conditions (distance, volume, pace, off-axis, background, moving)
  appear on both sides of the split.
* Hard negatives: 2 per phrase. ``hey bender's`` is excluded from training
  ENTIRELY (decision 2026-09-24): it contains the wake phrase, so teaching the
  model to reject it would suppress a legitimate wake. Its clips are recorded
  under ``watch`` and reported at evaluation without gating.
* Ambient: the last 5 minutes are held out for false-wakes-per-hour. The other
  15 become augmentation backgrounds, so the model hears this actual room.
  Held-out minutes are never used as backgrounds -- that would be leakage.
* Conversation (close-range speech, never the phrase): the last 3 minutes are
  held out; the rest are sliced into 2 s negatives for training. Ambient proved
  v0.1 has no false wakes on room sound, but the real risk is speech aimed at
  someone else NEXT TO the device, which no ambient minute contains.

Selection is a seeded shuffle, so re-running reproduces the same split; the
seed is stored in the file. Re-running with ``--force`` after more clips are
captured rewrites it, which invalidates comparisons against older runs.

Usage (on the device, where the samples live):
    venv/bin/python scripts/split_wake_samples.py
    venv/bin/python scripts/split_wake_samples.py --show
"""

import argparse
import collections
import json
import os
import random

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_ROOT = os.path.join(BASE_DIR, "data", "wake_samples")
SPLIT_PATH = os.path.join(OUT_ROOT, "split.json")

SEED = 20260924
HOLDOUT_PER_GROUP = 2
HOLDOUT_AMBIENT_MINUTES = 5
HOLDOUT_CONVERSATION_MINUTES = 3
# Contains the wake phrase; training it as a negative would teach the model to
# suppress a real wake. Kept on disk, reported at evaluation, never trained on.
EXCLUDED_NEGATIVE_PHRASES = ("hey_bender's",)


def _clips(mode: str) -> "list[str]":
    """Relative paths of every clip in a mode, sorted for determinism."""
    root = os.path.join(OUT_ROOT, mode)
    out = []
    for dirpath, _, names in os.walk(root):
        for n in sorted(names):
            if n.endswith(".wav"):
                out.append(os.path.relpath(os.path.join(dirpath, n), BASE_DIR))
    return sorted(out)


def _group(path: str) -> str:
    """Condition / phrase label, i.e. the filename without its clip number."""
    return os.path.basename(path).rsplit("_", 1)[0]


def build_split(per_group: int = HOLDOUT_PER_GROUP,
                ambient_minutes: int = HOLDOUT_AMBIENT_MINUTES,
                seed: int = SEED,
                conversation_minutes: int = HOLDOUT_CONVERSATION_MINUTES) -> dict:
    rng = random.Random(seed)
    split = {
        "seed": seed,
        "holdout_per_group": per_group,
        "excluded_negative_phrases": list(EXCLUDED_NEGATIVE_PHRASES),
        "positive": {"train": [], "holdout": []},
        "hard_negative": {"train": [], "holdout": [], "watch": []},
        "ambient": {"background": [], "holdout": []},
        "conversation": {"train": [], "holdout": []},
    }

    for mode in ("positive", "hard_negative"):
        groups = collections.OrderedDict()
        for p in _clips(mode):
            groups.setdefault(_group(p), []).append(p)
        for g, paths in groups.items():
            if mode == "hard_negative" and g in EXCLUDED_NEGATIVE_PHRASES:
                split[mode]["watch"].extend(paths)
                continue
            shuffled = paths[:]
            rng.shuffle(shuffled)
            k = min(per_group, max(0, len(shuffled) - 1))  # never hold out all
            split[mode]["holdout"].extend(sorted(shuffled[:k]))
            split[mode]["train"].extend(sorted(shuffled[k:]))

    ambient = _clips("ambient")
    if ambient_minutes >= len(ambient):
        raise SystemExit(
            f"ambient holdout ({ambient_minutes}) must be smaller than the "
            f"{len(ambient)} minutes captured")
    # Last N minutes, not a random N: consecutive audio keeps whatever was
    # happening in the room intact, and a per-hour rate wants continuous time.
    split["ambient"]["holdout"] = ambient[-ambient_minutes:]
    split["ambient"]["background"] = ambient[:-ambient_minutes]

    # Conversation is optional: round 1 did not capture it, and a split made
    # before it exists must stay valid rather than raise.
    conv = _clips("conversation")
    if conv:
        k = min(conversation_minutes, max(0, len(conv) - 1))
        split["conversation"]["holdout"] = conv[-k:] if k else []
        split["conversation"]["train"] = conv[:len(conv) - k]
    return split


def summarise(split: dict) -> str:
    lines = []
    for mode in ("positive", "hard_negative"):
        d = split[mode]
        extra = f", watch {len(d.get('watch', []))}" if d.get("watch") else ""
        lines.append(f"{mode:14s} train {len(d['train']):3d}  "
                     f"holdout {len(d['holdout']):3d}{extra}")
        groups = collections.Counter(_group(p) for p in d["holdout"])
        lines.append("               holdout per group: "
                     + ", ".join(f"{g}={n}" for g, n in sorted(groups.items())))
    a = split["ambient"]
    lines.append(f"{'ambient':14s} background {len(a['background']):3d} min  "
                 f"holdout {len(a['holdout']):3d} min")
    c = split.get("conversation", {"train": [], "holdout": []})
    lines.append(f"{'conversation':14s} train {len(c['train']):3d} min  "
                 f"holdout {len(c['holdout']):3d} min"
                 + ("   (none captured yet)" if not c["train"] and not c["holdout"] else ""))
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true",
                    help="rewrite an existing split (invalidates old comparisons)")
    ap.add_argument("--show", action="store_true", help="print the current split and exit")
    args = ap.parse_args()

    if args.show:
        if not os.path.exists(SPLIT_PATH):
            raise SystemExit(f"no split yet: {SPLIT_PATH}")
        print(summarise(json.load(open(SPLIT_PATH))))
        return

    if os.path.exists(SPLIT_PATH) and not args.force:
        print(f"Split already exists: {SPLIT_PATH}")
        print(summarise(json.load(open(SPLIT_PATH))))
        print("\nEvery run and evaluation must use this one. Pass --force to "
              "rewrite it (old model comparisons stop being like-for-like).")
        return

    split = build_split()
    os.makedirs(OUT_ROOT, exist_ok=True)
    with open(SPLIT_PATH, "w") as f:
        json.dump(split, f, indent=2)
        f.write("\n")
    print(f"Wrote {SPLIT_PATH}\n")
    print(summarise(split))


if __name__ == "__main__":
    main()
