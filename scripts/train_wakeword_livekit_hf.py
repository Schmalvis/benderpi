# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = [
#   "livekit-wakeword[train,eval,export]==0.2.1",
#   "huggingface_hub>=0.20.3",
#   "numpy<2",
#   "pyyaml",
# ]
# ///
"""Train a "hey bender" wake word with livekit-wakeword on Hugging Face Jobs.

WHY A SECOND TRAINER
--------------------
openWakeWord plateaus on this phrase. Measured on 26 clean held-out clips at
threshold 0.35, with the live 2-of-4 smoothing: v0.1 (synthetic positives only)
wakes on 2, and two retrains with real positives reach 6 and 8. The ship gate
is 80%. Three runs have not moved that ceiling, so this tries a different
classifier head (conv-attention) and a pipeline that generates confusable
negatives for us.

NOT A DROP-IN. Measured on-device 2026-09-29: the same model scoring the same
audio reads 0.965 through openWakeWord's streaming front-end and 0.005 through
livekit's stateless one. A livekit model must be scored and served by livekit's
own engine -- eval_wake_model.py picks the engine from the filename, which is
why the output name must contain "livekit".

Runtime cost of that engine, measured on the Pi 5: predict() takes 34.8 ms
because it re-scores a full 2s window each call. At an 80 ms hop that is 44% of
one core; at 160 ms, 22%. The wake loop is NOT wired to it yet, deliberately --
the model has to earn that work by passing the gates first.

RUN
    hf jobs uv run --flavor t4-small --timeout 5h --secrets HF_TOKEN \
        scripts/train_wakeword_livekit_hf.py -- \
        --n-samples 20000 --steps 50000 --use-real-samples

THEN, on the device
    venv/bin/python scripts/eval_wake_model.py \
        --model models/hey_bender_livekit_v1.onnx \
        --compare models/hey_bender_v0.1.onnx

REVERTING is nothing: this writes a new model file and touches no config. The
deployed model stays v0.1 until deploy_hey_bender.sh is run, and that refuses
a model which fails the gates.

Plan: docs/superpowers/plans/2026-09-24-wake-word-retrain-v0.2.md
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import wave

HF_MODEL_REPO = "Schmalvis/hey-bender-oww"
HF_SAMPLES_REPO = "Schmalvis/bender-wake-samples"
OUTPUT_ONNX_NAME = "hey_bender_livekit_v1.onnx"
WORK = "/tmp/lkwork"

# Phonetically close phrases. These are not guesses: they are the ones this
# household actually recorded as hard negatives, and v0.1 false-wakes on
# "hey vendor" (7/10) and "hey bend" (5/10). livekit generates its own
# adversarial set too; these are added on top.
NEGATIVE_PHRASES = [
    "bender", "hey bend", "hey vendor", "hey Brenda", "hey Ben",
    "blender", "gender", "surrender", "lavender", "remember",
    "play defender", "okay then", "hey there", "hey friend",
]

APT_PACKAGES = ("git", "ffmpeg", "libsndfile1", "espeak-ng", "sox")


def _run(cmd: str, cwd: "str | None" = None, check: bool = True) -> int:
    print(f"\n$ {cmd}", flush=True)
    r = subprocess.run(cmd, shell=True, cwd=cwd)
    if check and r.returncode != 0:
        raise RuntimeError(f"command failed ({r.returncode}): {cmd}")
    return r.returncode


def _apt_prereqs() -> None:
    if _run("apt-get update -qq && apt-get install -y -qq " + " ".join(APT_PACKAGES),
            check=False) != 0:
        print("WARNING: apt install failed; espeak-ng is required for phonemes.",
              flush=True)


def _strip_data_prefix(rel: str) -> str:
    marker = "data/wake_samples/"
    return rel[rel.index(marker) + len(marker):] if marker in rel else rel


def _load_split(root: str) -> dict:
    path = os.path.join(root, "split.json")
    if not os.path.exists(path):
        raise RuntimeError(f"{path} missing — the held-out clips are the experiment")
    return json.load(open(path))


def _seed_real_clips(root: str, split: dict, out_dir: str, model_name: str,
                     copies: int, neg_copies: int) -> dict:
    """Copy the real clips into livekit's generated-clip directories.

    Placed AFTER `generate` and before `augment`, so they are added on top of
    the synthetic set and then augmented identically. Held-out clips are
    asserted absent rather than merely skipped: one leak makes the recall
    number meaningless, and this is the failure that already cost two runs.
    """
    pos_dir = os.path.join(out_dir, model_name, "positive_train")
    neg_dir = os.path.join(out_dir, model_name, "negative_train")
    for d in (pos_dir, neg_dir):
        os.makedirs(d, exist_ok=True)

    holdout = set(split["positive"]["holdout"]) | set(split["hard_negative"]["holdout"])
    watch = set(split["hard_negative"].get("watch", []))
    pos = list(split["positive"]["train"])
    neg = list(split["hard_negative"]["train"])
    assert not (set(pos) | set(neg)) & holdout, "held-out clips leaked into training"
    assert not set(neg) & watch, "excluded phrase leaked into the negative set"
    if not pos:
        raise RuntimeError("split.json lists no training positives")

    written = {"positive": 0, "negative": 0}
    for rels, dest, k, key in ((pos, pos_dir, copies, "positive"),
                               (neg, neg_dir, neg_copies, "negative")):
        for rel in rels:
            src = os.path.join(root, _strip_data_prefix(rel))
            if not os.path.exists(src):
                raise RuntimeError(f"clip listed in split.json is missing: {src}")
            stem = os.path.splitext(os.path.basename(rel))[0]
            for i in range(k):
                shutil.copyfile(src, os.path.join(dest, f"real_{stem}_{i:03d}.wav"))
                written[key] += 1

    n_pos = len([f for f in os.listdir(pos_dir) if f.endswith(".wav")])
    print(f"Seeded {written['positive']} real positives ({len(pos)} clips x {copies}) "
          f"and {written['negative']} real negatives.")
    print(f"  positive_train now holds {n_pos} clips; real share "
          f"{100.0 * written['positive'] / max(1, n_pos):.1f}%")
    print(f"  held out, never copied: {len(split['positive']['holdout'])} positive, "
          f"{len(split['hard_negative']['holdout'])} negative")
    return written


def _seed_conversation_negatives(root: str, split: dict, out_dir: str,
                                 model_name: str, clip_s: float = 2.0,
                                 rate: int = 16000) -> int:
    """Close-range household speech, sliced into negative clips."""
    import numpy as np

    files = split.get("conversation", {}).get("train", [])
    if not files:
        return 0
    dest = os.path.join(out_dir, model_name, "negative_train")
    os.makedirs(dest, exist_ok=True)
    n, want = 0, int(rate * clip_s)
    for rel in files:
        src = os.path.join(root, _strip_data_prefix(rel))
        if not os.path.exists(src):
            continue
        with wave.open(src) as w:
            pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        stem = os.path.splitext(os.path.basename(rel))[0]
        for i in range(0, len(pcm) - want + 1, want):
            out = os.path.join(dest, f"real_conv_{stem}_{i // want:03d}.wav")
            with wave.open(out, "wb") as w2:
                w2.setnchannels(1)
                w2.setsampwidth(2)
                w2.setframerate(rate)
                w2.writeframes(pcm[i:i + want].tobytes())
            n += 1
    print(f"Seeded {n} conversational negatives from {len(files)} minutes.")
    return n


def _room_backgrounds(root: str, split: dict, work: str) -> "str | None":
    """This room's ambient minutes, as augmentation background.

    Held-out minutes stay out: using them as backgrounds and then counting
    false wakes on them measures nothing.
    """
    files = split.get("ambient", {}).get("background", [])
    if not files:
        return None
    dest = os.path.join(work, "room_ambient")
    os.makedirs(dest, exist_ok=True)
    for rel in files:
        src = os.path.join(root, _strip_data_prefix(rel))
        if os.path.exists(src):
            shutil.copyfile(src, os.path.join(dest, os.path.basename(rel)))
    print(f"Room backgrounds: {len(os.listdir(dest))} minutes from this room.")
    return dest


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phrase", default="hey bender")
    ap.add_argument("--model-name", default="hey_bender_livekit")
    ap.add_argument("--n-samples", type=int, default=20000)
    ap.add_argument("--n-samples-val", type=int, default=4000)
    ap.add_argument("--steps", type=int, default=50000)
    ap.add_argument("--model-type", default="conv_attention",
                    choices=["conv_attention", "dnn", "rnn"])
    ap.add_argument("--model-size", default="small",
                    choices=["tiny", "small", "medium", "large"])
    ap.add_argument("--target-fp-per-hour", type=float, default=0.5)
    ap.add_argument("--max-negative-weight", type=int, default=1000)
    ap.add_argument("--augmentation-rounds", type=int, default=3)
    ap.add_argument("--use-real-samples", action="store_true")
    ap.add_argument("--real-positive-copies", type=int, default=40)
    ap.add_argument("--real-negative-copies", type=int, default=20)
    ap.add_argument("--samples-repo", default=HF_SAMPLES_REPO)
    ap.add_argument("--model-repo", default=HF_MODEL_REPO)
    ap.add_argument("--output-name", default=OUTPUT_ONNX_NAME)
    args = ap.parse_args()

    if "livekit" not in args.output_name.lower():
        sys.exit("--output-name must contain 'livekit': eval_wake_model.py picks "
                 "the engine from the filename, and scoring a livekit model with "
                 "openWakeWord's front-end silently reports ~0.005.")
    if not (os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")):
        sys.exit("HF_TOKEN is not set. Pass --secrets HF_TOKEN to `hf jobs uv run`.")
    os.environ.setdefault("HF_TOKEN", os.environ.get("HUGGINGFACE_TOKEN", ""))

    os.makedirs(WORK, exist_ok=True)
    print("=== 1/6 System packages ===", flush=True)
    _apt_prereqs()

    print("=== 2/6 Fetch the real clips ===", flush=True)
    root = split = None
    if args.use_real_samples:
        from huggingface_hub import snapshot_download
        root = snapshot_download(repo_id=args.samples_repo, repo_type="dataset",
                                 local_dir=os.path.join(WORK, "wake_samples"))
        split = _load_split(root)
    else:
        print("Synthetic-only run.")

    data_dir = os.path.join(WORK, "data")
    out_dir = os.path.join(WORK, "output")
    background_paths = [os.path.join(data_dir, "backgrounds")]
    if split is not None:
        room = _room_backgrounds(root, split, WORK)
        if room:
            background_paths.append(room)

    # A YAML config driving the documented CLI stages, rather than the Python
    # API. The CLI's config keys ARE the documented contract (configs/prod.yaml);
    # the dataclass field names are not, and a setting silently dropped because
    # a field was renamed upstream would cost a whole run to notice.
    cfg = {
        "model_name": args.model_name,
        "target_phrases": [args.phrase],
        "n_samples": args.n_samples,
        "n_samples_val": args.n_samples_val,
        "n_background_samples": max(500, args.n_samples // 10),
        "n_background_samples_val": max(100, args.n_samples_val // 10),
        "custom_negative_phrases": list(NEGATIVE_PHRASES),
        "data_dir": data_dir,
        "output_dir": out_dir,
        "steps": args.steps,
        "target_fp_per_hour": args.target_fp_per_hour,
        "max_negative_weight": args.max_negative_weight,
        "model": {"model_type": args.model_type, "model_size": args.model_size},
        "augmentation": {
            "clip_duration": 2.0,
            "rounds": args.augmentation_rounds,
            "background_paths": background_paths,
            "rir_paths": [os.path.join(data_dir, "rirs")],
        },
    }
    import yaml
    cfg_path = os.path.join(WORK, "bender.yaml")
    with open(cfg_path, "w") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)
    print("config:\n" + open(cfg_path).read(), flush=True)

    print("=== 3/6 setup + generate ===", flush=True)
    _run(f'livekit-wakeword setup --config "{cfg_path}"')
    _run(f'livekit-wakeword generate "{cfg_path}"')

    if args.use_real_samples:
        print("=== 4/6 Seed real clips ===", flush=True)
        _seed_real_clips(root, split, out_dir, args.model_name,
                         args.real_positive_copies, args.real_negative_copies)
        _seed_conversation_negatives(root, split, out_dir, args.model_name)

    print("=== 5/6 augment + train ===", flush=True)
    _run(f'livekit-wakeword augment "{cfg_path}"')    # augment + feature extraction
    _run(f'livekit-wakeword train "{cfg_path}"')

    print("=== 6/6 export + upload ===", flush=True)
    _run(f'livekit-wakeword export "{cfg_path}"')
    _run(f'livekit-wakeword eval "{cfg_path}"', check=False)

    import glob
    hits = [h for h in glob.glob(os.path.join(out_dir, "**", "*.onnx"), recursive=True)
            if not any(x in os.path.basename(h).lower()
                       for x in ("melspec", "embedding"))]
    if not hits:
        raise RuntimeError(f"no exported ONNX found under {out_dir}")
    onnx_path = max(hits, key=os.path.getmtime)
    print(f"exported: {onnx_path}")
    print("NB: any eval printed above is livekit's OWN synthetic validation set, "
          "not this household's held-out clips. The device decides.")

    from huggingface_hub import HfApi
    api = HfApi()
    api.create_repo(repo_id=args.model_repo, exist_ok=True)
    api.upload_file(path_or_fileobj=str(onnx_path), path_in_repo=args.output_name,
                    repo_id=args.model_repo)
    print(f"\nDone: https://huggingface.co/{args.model_repo}/blob/main/{args.output_name}")
    print("\nScore it on the DEVICE — nothing is deployed by this job:")
    print(f"  venv/bin/python scripts/eval_wake_model.py "
          f"--model models/{args.output_name} --compare models/hey_bender_v0.1.onnx")


if __name__ == "__main__":
    main()
