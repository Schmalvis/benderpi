# /// script
# requires-python = ">=3.11,<3.12"
# dependencies = [
#   "torch==2.3.1",
#   "torchaudio==2.3.1",
#   "numpy<2",
#   "scipy==1.11.4",
#   "soundfile",
#   "librosa",
#   "pyarrow<15",
#   "datasets==2.14.6",
#   "huggingface_hub>=0.20.3",
#   "tqdm",
#   "pyyaml",
#   "onnx",
#   "onnxruntime",
#   "onnxscript",
#   "torchmetrics==1.2.0",
#   "torch_audiomentations==0.11.0",
#   "audiomentations==0.33.0",
#   "speechbrain==0.5.14",
#   "acoustics",
#   "pronouncing",
#   "deep-phonemizer==0.0.19",
#   "webrtcvad-wheels",   # NOT webrtcvad: that builds from C source and the
#                         # job image has no Python headers or compiler
#   "torchinfo",
#   "espeak-phonemizer",
#   "mutagen",
#   "tensorflow-cpu",
#   "openwakeword",
# ]
# ///
"""Train the "hey bender" wake word on Hugging Face Jobs.

Runs the full openWakeWord pipeline on HF GPU infrastructure and uploads the
resulting ONNX to Schmalvis/hey-bender-oww. Replaces the Modal version
(scripts/train_hey_bender.py, kept for reference): the model repo, the sample
data and the account credit are all already on Hugging Face, so a second
provider bought nothing.

SETUP (once)
    hf auth login                       # a token with write access
    venv/bin/python scripts/upload_wake_samples.py      # private dataset repo

RUN
    hf jobs uv run --flavor t4-small --timeout 5h \
        --secrets HF_TOKEN \
        scripts/train_hey_bender_hf.py -- \
        --n-samples 20000 --steps 50000 --use-real-samples \
        --real-positive-fraction 0.20 --output-name hey_bender_v0.2_r20.onnx

    hf jobs logs <job-id> --follow

The ratio sweep from the plan is three of those, differing only in
--real-positive-fraction (0.10 / 0.20 / 0.35) and --output-name. They are
independent and can run at the same time.

THEN, on the device (never here -- the whole defect is a synthetic-to-real gap):
    bash scripts/deploy_hey_bender.sh hey_bender_v0.2_r20.onnx --threshold 0.35

HOW REAL CLIPS ENTER TRAINING
    openwakeword/train.py --generate_clips counts what is already in
    positive_train/ and generates only `n_samples - n_current`, so pre-placed
    real clips inject themselves AND reduce the synthetic count by the same
    number. --augment_clips then globs those directories, so every copy gets
    independent RIR + background augmentation. Measured 2026-09-28 against
    v0.1 on held-out clips: recall 2/26, and 0/12 on ordinary speech. It never
    learned the phrase; it learned the synthetic voice's cadence.

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
HF_SAMPLES_REPO = "Schmalvis/bender-wake-samples"   # private dataset
OUTPUT_ONNX_NAME = "hey_bender_v0.2.onnx"
WORK = "/tmp/work"

# System packages the audio stack needs. The uv image is Debian-based and jobs
# run as root, so this is available; it is best-effort because a custom
# --image may already carry them.
APT_PACKAGES = ("git", "ffmpeg", "libsndfile1", "espeak-ng")


def _run(cmd: str, cwd: "str | None" = None, check: bool = True):
    print(f"\n$ {cmd}", flush=True)
    r = subprocess.run(cmd, shell=True, cwd=cwd)
    if check and r.returncode != 0:
        raise RuntimeError(f"command failed ({r.returncode}): {cmd}")
    return r.returncode


def _apt_prereqs():
    missing = [p for p in APT_PACKAGES if shutil.which(p.split("-")[0]) is None]
    if not missing and shutil.which("espeak-ng"):
        print("System packages already present.")
        return
    rc = _run("apt-get update -qq && apt-get install -y -qq "
              + " ".join(APT_PACKAGES), check=False)
    if rc != 0:
        print("WARNING: apt install failed. If the run dies in espeak/ffmpeg, "
              "pass --image with a base that already has them.", flush=True)


# ---------------------------------------------------------------------------
# Real-voice clips (see module docstring for the mechanism)
# ---------------------------------------------------------------------------
def _strip_data_prefix(rel: str) -> str:
    """split.json stores repo-relative paths (data/wake_samples/...); the
    downloaded dataset is rooted at the wake_samples directory itself."""
    marker = "data/wake_samples/"
    return rel[rel.index(marker) + len(marker):] if marker in rel else rel


def _load_split(root: str) -> dict:
    """Read the frozen train/holdout split. Absent = refuse to train.

    Training without it would silently use the held-out clips too, and the
    recall number it produces would be meaningless.
    """
    path = os.path.join(root, "split.json")
    if not os.path.exists(path):
        raise RuntimeError(
            f"{path} missing. Run scripts/split_wake_samples.py on the device, "
            "re-upload with scripts/upload_wake_samples.py, and try again; the "
            "held-out clips are the experiment.")
    return json.load(open(path))


def _seed_real_clips(root: str, split: dict, positive_train_dir: str,
                     negative_train_dir: str, n_samples: int,
                     positive_fraction: float, negative_copies: int) -> dict:
    """Copy real clips into the generator's output directories.

    Each clip is written `k` times under distinct names; `k` sets the real
    share of the positive set. Held-out clips are asserted absent rather than
    merely skipped -- one silent leak makes the whole run worthless.
    """
    for d in (positive_train_dir, negative_train_dir):
        os.makedirs(d, exist_ok=True)

    holdout = set(split["positive"]["holdout"]) | set(split["hard_negative"]["holdout"])
    watch = set(split["hard_negative"].get("watch", []))
    pos = list(split["positive"]["train"])
    neg = list(split["hard_negative"]["train"])
    assert not (set(pos) | set(neg)) & holdout, "held-out clips leaked into training"
    assert not set(neg) & watch, "excluded phrase leaked into the negative set"
    if not pos:
        raise RuntimeError("split.json lists no training positives")

    pos_copies = max(1, round(n_samples * positive_fraction / len(pos)))
    written = {"positive": 0, "negative": 0,
               "positive_copies": pos_copies, "negative_copies": negative_copies}
    for rel_paths, dest, copies, key in (
        (pos, positive_train_dir, pos_copies, "positive"),
        (neg, negative_train_dir, negative_copies, "negative"),
    ):
        for rel in rel_paths:
            src = os.path.join(root, _strip_data_prefix(rel))
            if not os.path.exists(src):
                raise RuntimeError(f"clip listed in split.json is missing: {src}")
            stem = os.path.splitext(os.path.basename(rel))[0]
            for i in range(copies):
                shutil.copyfile(src, os.path.join(dest, f"real_{stem}_{i:03d}.wav"))
                written[key] += 1

    print(f"Seeded real clips: {written['positive']} positive "
          f"({len(pos)} clips x {pos_copies}), {written['negative']} negative "
          f"({len(neg)} clips x {negative_copies}).")
    print(f"  real share of the {n_samples}-sample positive set: "
          f"{100.0 * written['positive'] / n_samples:.1f}%")
    print(f"  held out, never copied: {len(split['positive']['holdout'])} positive, "
          f"{len(split['hard_negative']['holdout'])} negative, {len(watch)} watch-only")
    return written


def _seed_conversation_negatives(root: str, split: dict, negative_train_dir: str,
                                 clip_s: float = 2.0, rate: int = 16000) -> int:
    """Slice close-range conversational speech into negative training clips.

    Ambient room sound scores 0 frames over threshold on v0.1, so it proves
    little. The untested case is someone talking NEXT TO the device to another
    person. Held-out minutes stay out: they measure false wakes per hour.
    """
    import numpy as np

    files = split.get("conversation", {}).get("train", [])
    if not files:
        print("No conversational negatives in the split (optional).")
        return 0
    os.makedirs(negative_train_dir, exist_ok=True)
    n, want = 0, int(rate * clip_s)
    for rel in files:
        src = os.path.join(root, _strip_data_prefix(rel))
        if not os.path.exists(src):
            continue
        with wave.open(src) as w:
            pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        stem = os.path.splitext(os.path.basename(rel))[0]
        for i in range(0, len(pcm) - want + 1, want):
            out = os.path.join(negative_train_dir, f"real_conv_{stem}_{i // want:03d}.wav")
            with wave.open(out, "wb") as w2:
                w2.setnchannels(1)
                w2.setsampwidth(2)
                w2.setframerate(rate)
                w2.writeframes(pcm[i:i + want].tobytes())
            n += 1
    print(f"Seeded {n} conversational negative clips from {len(files)} minutes.")
    return n


def _ambient_background_dir(root: str, split: dict, work: str) -> "str | None":
    """Copy the held-IN ambient minutes into their own directory, to be used as
    augmentation backgrounds. Held-out minutes stay out: using them as
    backgrounds and then scoring false wakes on them is leakage."""
    files = split.get("ambient", {}).get("background", [])
    if not files:
        return None
    dest = os.path.join(work, "room_ambient")
    os.makedirs(dest, exist_ok=True)
    for rel in files:
        src = os.path.join(root, _strip_data_prefix(rel))
        if os.path.exists(src):
            shutil.copyfile(src, os.path.join(dest, os.path.basename(rel)))
    print(f"Room ambient backgrounds: {len(os.listdir(dest))} minutes from this room.")
    return dest


def _download_samples(work: str, repo: str) -> str:
    """Fetch the private samples dataset (clips + split.json)."""
    from huggingface_hub import snapshot_download

    print(f"Downloading samples from {repo} ...", flush=True)
    return snapshot_download(repo_id=repo, repo_type="dataset",
                             local_dir=os.path.join(work, "wake_samples"))


# ---------------------------------------------------------------------------
# openWakeWord pipeline
# ---------------------------------------------------------------------------
def _clone_repos(work: str):
    oww = os.path.join(work, "openWakeWord")
    psg = os.path.join(work, "piper-sample-generator")
    if not os.path.exists(oww):
        _run("git clone -q https://github.com/dscripka/openWakeWord", cwd=work)
    if not os.path.exists(psg):
        _run("git clone -q https://github.com/dscripka/piper-sample-generator", cwd=work)
    assert os.path.exists(os.path.join(psg, "generate_samples.py")), (
        "generate_samples.py missing from piper-sample-generator (dscripka fork)")

    # Upstream bug: --convert_to_tflite's argparse default is the *string*
    # "False", which is truthy, so the tflite branch fires even when the flag
    # is never passed. We only want ONNX.
    train_py = os.path.join(oww, "openwakeword", "train.py")
    content = open(train_py).read()
    patched = content.replace('action="store_true",\n        default="False",',
                              'action="store_true",\n        default=False,')
    assert patched != content, "convert_to_tflite default string not found to patch"
    open(train_py, "w").write(patched)
    return oww, psg


def _download_piper_voice(psg: str) -> str:
    """generate_samples.py hardcodes models/en-us-libritts-high.pt, which only
    exists on the v1.0.0 release tag."""
    import urllib.request

    models_dir = os.path.join(psg, "models")
    os.makedirs(models_dir, exist_ok=True)
    target = os.path.join(models_dir, "en-us-libritts-high.pt")
    if not os.path.exists(target):
        urllib.request.urlretrieve(
            "https://github.com/rhasspy/piper-sample-generator/releases/"
            "download/v1.0.0/en-us-libritts-high.pt", target)
    return target


def _download_oww_feature_models():
    """AudioFeatures needs melspectrogram.onnx + embedding_model.onnx, which
    are release assets rather than wheel contents. The bogus model name skips
    the large pretrained-wakeword download."""
    from openwakeword.utils import download_models
    download_models(model_names=["_none_"])


def _download_training_data(work: str):
    """RIRs + FMA backgrounds + ACAV100M negative features + validation set."""
    import numpy as np
    import scipy.io.wavfile
    from datasets import Audio, load_dataset
    from huggingface_hub import hf_hub_download
    from tqdm import tqdm

    rir_dir = os.path.join(work, "mit_rirs")
    if not os.path.exists(rir_dir):
        os.makedirs(rir_dir)
        ds = load_dataset("davidscripka/MIT_environmental_impulse_responses",
                          split="train", streaming=True
                          ).cast_column("audio", Audio(sampling_rate=16000))
        for row in tqdm(ds, desc="MIT RIRs"):
            name = row["audio"]["path"].split("/")[-1].replace(".mp3", ".wav")
            scipy.io.wavfile.write(os.path.join(rir_dir, name), 16000,
                                   (row["audio"]["array"] * 32767).astype(np.int16))

    fma_dir = os.path.join(work, "fma")
    if not os.path.exists(fma_dir):
        os.makedirs(fma_dir)
        ds = load_dataset("rudraml/fma", name="small", split="train", streaming=True
                          ).cast_column("audio", Audio(sampling_rate=16000))
        n = 0
        for row in tqdm(ds, desc="FMA music"):
            scipy.io.wavfile.write(os.path.join(fma_dir, f"fma_{n:05d}.wav"), 16000,
                                   (row["audio"]["array"] * 32767).astype(np.int16))
            n += 1
            if n >= 200:
                break

    for fn in ("openwakeword_features_ACAV100M_2000_hrs_16bit.npy",
               "validation_set_features.npy"):
        if not os.path.exists(os.path.join(work, fn)):
            hf_hub_download(repo_id="davidscripka/openwakeword_features", filename=fn,
                            repo_type="dataset", local_dir=work)


def _write_config(work: str, oww: str, psg: str, phrase: str, n_samples: int,
                  n_samples_val: int, steps: int, piper_pt: str,
                  target_fp_per_hour: float, max_negative_weight: int,
                  augmentation_rounds: int, target_recall: float,
                  room_ambient_dir: "str | None" = None,
                  room_ambient_weight: int = 3) -> str:
    import yaml

    config = yaml.load(open(os.path.join(oww, "examples", "custom_model.yml")).read(),
                       yaml.Loader)
    config["target_phrase"] = [phrase]
    config["model_name"] = phrase.replace(" ", "_")
    config["n_samples"] = n_samples
    config["n_samples_val"] = n_samples_val
    config["steps"] = steps
    config["target_accuracy"] = 0.7
    config["target_recall"] = target_recall
    # Library defaults (0.2 fp/hour, weight 1500) over-suppress false positives
    # at recall's expense, and recall is the entire problem here.
    config["target_false_positives_per_hour"] = target_fp_per_hour
    config["max_negative_weight"] = max_negative_weight
    config["augmentation_rounds"] = augmentation_rounds
    # FMA music is generic; the room ambient is this room, this mic, these
    # gains. Weight the room up so augmentation is dominated by the acoustic
    # environment the model actually has to work in.
    config["background_paths"] = [os.path.join(work, "fma")]
    config["background_paths_duplication_rate"] = [1]
    if room_ambient_dir:
        config["background_paths"].append(room_ambient_dir)
        config["background_paths_duplication_rate"].append(room_ambient_weight)
    config["rir_paths"] = [os.path.join(work, "mit_rirs")]
    config["piper_sample_generator_path"] = psg
    config["piper_model"] = piper_pt
    config["false_positive_validation_data_path"] = os.path.join(
        work, "validation_set_features.npy")
    config["feature_data_files"] = {"ACAV100M_sample": os.path.join(
        work, "openwakeword_features_ACAV100M_2000_hrs_16bit.npy")}
    config["output_dir"] = os.path.join(work, "my_custom_model")

    cfg_path = os.path.join(work, "my_model.yaml")
    with open(cfg_path, "w") as f:
        yaml.dump(config, f)
    print("Training config:")
    for k in ("target_phrase", "model_name", "n_samples", "steps", "output_dir"):
        print(f"  {k}: {config[k]}")
    return cfg_path


def _find_onnx(work: str) -> str:
    import glob
    hits = sorted(glob.glob(os.path.join(work, "my_custom_model", "**", "*.onnx"),
                            recursive=True))
    hits = [h for h in hits if "melspectrogram" not in h and "embedding" not in h]
    if not hits:
        raise RuntimeError(f"no ONNX produced under {work}/my_custom_model")
    return hits[-1]


def _upload(onnx_path: str, repo: str, output_name: str) -> str:
    from huggingface_hub import HfApi
    api = HfApi()
    api.create_repo(repo_id=repo, exist_ok=True)
    api.upload_file(path_or_fileobj=onnx_path, path_in_repo=output_name,
                    repo_id=repo)
    return f"https://huggingface.co/{repo}/blob/main/{output_name}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phrase", default="hey bender")
    ap.add_argument("--n-samples", type=int, default=20000)
    ap.add_argument("--n-samples-val", type=int, default=1000)
    ap.add_argument("--steps", type=int, default=50000)
    ap.add_argument("--target-fp-per-hour", type=float, default=1.0)
    ap.add_argument("--max-negative-weight", type=int, default=500)
    ap.add_argument("--augmentation-rounds", type=int, default=2)
    ap.add_argument("--target-recall", type=float, default=0.5)
    ap.add_argument("--use-real-samples", action="store_true")
    ap.add_argument("--real-positive-fraction", type=float, default=0.20)
    ap.add_argument("--real-negative-copies", type=int, default=25)
    ap.add_argument("--room-ambient-weight", type=int, default=3)
    ap.add_argument("--samples-repo", default=HF_SAMPLES_REPO)
    ap.add_argument("--model-repo", default=HF_MODEL_REPO)
    ap.add_argument("--output-name", default=OUTPUT_ONNX_NAME)
    args = ap.parse_args()

    if not (os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")):
        sys.exit("HF_TOKEN is not set. Pass --secrets HF_TOKEN to `hf jobs uv run`.")
    os.environ.setdefault("HF_TOKEN", os.environ.get("HUGGINGFACE_TOKEN", ""))

    os.makedirs(WORK, exist_ok=True)
    print("=== 1/6 System packages ===", flush=True)
    _apt_prereqs()

    print("=== 2/6 Clone repos ===", flush=True)
    oww, psg = _clone_repos(WORK)
    sys.path.insert(0, psg)          # train.py imports generate_samples from here

    print("=== 3/6 Download models + training data ===", flush=True)
    piper_pt = _download_piper_voice(psg)
    _download_oww_feature_models()
    _download_training_data(WORK)

    print("=== 4/6 Seed clips + write config ===", flush=True)
    out_dir = os.path.join(WORK, "my_custom_model")
    model_name = args.phrase.replace(" ", "_")
    positive_train_dir = os.path.join(out_dir, model_name, "positive_train")
    negative_train_dir = os.path.join(out_dir, model_name, "negative_train")

    room_ambient_dir = None
    if args.use_real_samples:
        root = _download_samples(WORK, args.samples_repo)
        split = _load_split(root)
        _seed_real_clips(root, split, positive_train_dir, negative_train_dir,
                         args.n_samples, args.real_positive_fraction,
                         args.real_negative_copies)
        _seed_conversation_negatives(root, split, negative_train_dir)
        room_ambient_dir = _ambient_background_dir(root, split, WORK)
    else:
        print("No real samples requested: synthetic-only run (the v0.1 recipe).")

    cfg = _write_config(WORK, oww, psg, args.phrase, args.n_samples,
                        args.n_samples_val, args.steps, piper_pt,
                        args.target_fp_per_hour, args.max_negative_weight,
                        args.augmentation_rounds, args.target_recall,
                        room_ambient_dir, args.room_ambient_weight)

    print("=== 5/6 Train ===", flush=True)
    train_py = os.path.join(oww, "openwakeword", "train.py")
    env = f'PYTHONPATH="{psg}:$PYTHONPATH"'
    for label, flag in (("generate clips", "--generate_clips"),
                        ("augment clips", "--augment_clips"),
                        ("train model", "--train_model")):
        print(f"--- {label} ---", flush=True)
        _run(f'{env} "{sys.executable}" "{train_py}" --training_config "{cfg}" {flag}',
             cwd=WORK)

    print("=== 6/6 Upload ===", flush=True)
    onnx = _find_onnx(WORK)
    print(f"Found model: {onnx}")
    url = _upload(onnx, args.model_repo, args.output_name)
    print(f"\nDone: {url}")
    print("\nEvaluate on the DEVICE before deploying — synthetic metrics do not "
          "predict real-mic recall:")
    print(f"  venv/bin/python scripts/eval_wake_model.py --model models/{args.output_name}")
    print(f"  bash scripts/deploy_hey_bender.sh {args.output_name} --threshold 0.35")


if __name__ == "__main__":
    main()
