"""Modal training script for the "hey bender" openWakeWord model.

Runs the full openWakeWord training pipeline unattended on a Modal T4 GPU and
uploads the resulting ONNX model to the HF Hub repo ``Schmalvis/hey-bender-oww``.

This mirrors the (abandoned, Colab-only) notebook
``notebooks/hey_bender_training.ipynb`` but is fully hands-off: one command,
no browser interaction.

Setup (run once):
    pip install modal
    modal token new                       # browser auth, one time
    modal secret create huggingface HUGGINGFACE_TOKEN=hf_xxx   # write-scoped token

Run:
    modal run scripts/train_hey_bender.py

Higher-quality re-run (more samples / steps — see plan doc):
    modal run scripts/train_hey_bender.py --n-samples 25000 --steps 50000

v0.2 — real-voice positives (the ratio sweep in
docs/superpowers/plans/2026-09-24-wake-word-retrain-v0.2.md). Upload the
captures once, then one run per ratio:
    rsync -a pi@BenderPi.local:/home/pi/bender/data/wake_samples/ /tmp/wake_samples/
    modal volume create bender-wake-samples
    modal volume put bender-wake-samples /tmp/wake_samples /
    modal run scripts/train_hey_bender.py --n-samples 20000 --steps 50000 \
        --use-real-samples --real-positive-fraction 0.20 \
        --output-name hey_bender_v0.2_r20.onnx

Recall-focused re-run (loosens the FP-rate auto-tuning that was crushing
recall in earlier runs — see docs/checkpoints or memory for the 0.728/0.457
and 0.7185/0.439 baselines):
    modal run scripts/train_hey_bender.py --n-samples 25000 --steps 50000 \
        --target-fp-per-hour 1.0 --max-negative-weight 500 --augmentation-rounds 2

The model lands at:
    https://huggingface.co/Schmalvis/hey-bender-oww/blob/main/hey_bender_v0.1.onnx

Deploy to the Pi with: scripts/deploy_hey_bender.sh
"""

import modal

APP_NAME = "hey-bender-oww-train"
HF_REPO = "Schmalvis/hey-bender-oww"
OUTPUT_ONNX_NAME = "hey_bender_v0.1.onnx"

# Modal volume holding the real-voice captures recorded through the device's
# own mic (scripts/capture_wake_samples.py). Never in git -- household audio.
#   modal volume create bender-wake-samples
#   modal volume put bender-wake-samples /tmp/wake_samples /
REAL_SAMPLES_VOLUME = "bender-wake-samples"
REAL_SAMPLES_MOUNT = "/root/wake_samples"

# ---------------------------------------------------------------------------
# Container image: CUDA-capable torch + the openWakeWord training stack.
# Versions are pinned to what the notebook proved working (torch 2.x +
# torchaudio 2.x compatibility, onnxscript for ONNX export).
# ---------------------------------------------------------------------------
image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("git", "ffmpeg", "libsndfile1", "espeak-ng")
    .pip_install(
        "torch==2.3.1",
        "torchaudio==2.3.1",
        "numpy<2",
        "scipy==1.11.4",
        "soundfile",
        "librosa",
        "pyarrow<15",
        "datasets==2.14.6",
        "huggingface_hub==0.20.3",
        "tqdm",
        "pyyaml",
        "onnx",
        "onnxruntime",
        "onnxscript",
        "torchmetrics==1.2.0",
        "torch_audiomentations==0.11.0",
        "audiomentations==0.33.0",
        "speechbrain==0.5.14",
        "acoustics",
        "pronouncing",
        "deep-phonemizer==0.0.19",
        "webrtcvad",
        "torchinfo",
        "espeak-phonemizer",
        "mutagen",
        "tensorflow-cpu",  # train.py uses tf for the spectrogram feature model
        "openwakeword",
    )
)

app = modal.App(APP_NAME)


# ---------------------------------------------------------------------------
# Real-voice positives (v0.2). Why this works the way it does:
#
# openwakeword/train.py --generate_clips counts what is already in
# positive_train/ and generates only `n_samples - n_current`. So copying real
# clips in BEFORE that stage injects them AND reduces the synthetic count by
# the same number -- the mix ratio needs no new config key upstream.
# --augment_clips then globs those directories, so real clips get exactly the
# same RIR + background augmentation as synthetic ones, and every duplicate
# copy is augmented independently.
#
# Measured 2026-09-22 against v0.1: 6/100 real positives would wake it, while
# 22/90 near-miss phrases would. It never learned the phrase -- it learned the
# synthetic voice's cadence. That is what these clips are here to fix.
# Plan: docs/superpowers/plans/2026-09-24-wake-word-retrain-v0.2.md
# ---------------------------------------------------------------------------
def _load_split(root: str) -> dict:
    """Read the frozen train/holdout split. Absent = refuse to train.

    Training without it would silently use the held-out clips too, and the
    resulting recall number would be meaningless. Fail loudly instead.
    """
    import json
    import os

    path = os.path.join(root, "split.json")
    if not os.path.exists(path):
        raise RuntimeError(
            f"{path} missing. Run scripts/split_wake_samples.py on the device "
            "and re-upload the volume; the held-out clips are the experiment.")
    return json.load(open(path))


def _seed_real_clips(root: str, split: dict, positive_train_dir: str,
                     negative_train_dir: str, n_samples: int,
                     positive_fraction: float, negative_copies: int) -> dict:
    """Copy real clips into the generator's output directories.

    Each clip is written `k` times under distinct names; `k` sets the real
    share of the positive set, because the generator then makes that many
    fewer synthetic clips. Held-out clips are asserted absent rather than
    merely skipped.
    """
    import os
    import shutil

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
          f"{len(split['hard_negative']['holdout'])} negative, "
          f"{len(watch)} watch-only")
    return written


def _strip_data_prefix(rel: str) -> str:
    """split.json paths are repo-relative (data/wake_samples/...); the volume
    is mounted at the wake_samples directory itself."""
    marker = "data/wake_samples/"
    return rel[rel.index(marker) + len(marker):] if marker in rel else rel


def _seed_conversation_negatives(root: str, split: dict, negative_train_dir: str,
                                 clip_s: float = 2.0, rate: int = 16000) -> int:
    """Slice close-range conversational speech into negative training clips.

    Ambient room sound already scores 0 frames over threshold on v0.1, so it
    proves little. The untested case is someone talking NEXT TO the device to
    another person -- real speech, real level, not the phrase. train.py wants
    short clips in negative_train/, so the minutes are cut into `clip_s`
    chunks. Held-out minutes stay out: they measure false wakes per hour.
    """
    import os
    import wave

    import numpy as np

    files = split.get("conversation", {}).get("train", [])
    if not files:
        print("No conversational negatives in the split (optional).")
        return 0
    os.makedirs(negative_train_dir, exist_ok=True)
    n = 0
    want = int(rate * clip_s)
    for rel in files:
        src = os.path.join(root, _strip_data_prefix(rel))
        if not os.path.exists(src):
            continue
        with wave.open(src) as w:
            pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        stem = os.path.splitext(os.path.basename(rel))[0]
        for i in range(0, len(pcm) - want + 1, want):
            chunk = pcm[i:i + want]
            out = os.path.join(negative_train_dir, f"real_conv_{stem}_{i // want:03d}.wav")
            with wave.open(out, "wb") as w2:
                w2.setnchannels(1)
                w2.setsampwidth(2)
                w2.setframerate(rate)
                w2.writeframes(chunk.tobytes())
            n += 1
    print(f"Seeded {n} conversational negative clips from "
          f"{len(files)} minutes of close-range speech.")
    return n


def _ambient_background_dir(root: str, split: dict, work: str) -> "str | None":
    """Link the held-IN ambient minutes into their own directory, for use as
    augmentation backgrounds. The held-out minutes stay out: using them as
    backgrounds and then scoring false wakes on them is leakage."""
    import os
    import shutil

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


# ---------------------------------------------------------------------------
# Helpers (run inside the container).
# ---------------------------------------------------------------------------
def _run(cmd: str, cwd: str | None = None):
    """Run a shell command, streaming output, raising on failure."""
    import subprocess

    print(f"\n$ {cmd}", flush=True)
    r = subprocess.run(cmd, shell=True, cwd=cwd)
    if r.returncode != 0:
        raise RuntimeError(f"command failed ({r.returncode}): {cmd}")


def _clone_repos(work: str):
    import os

    oww = os.path.join(work, "openWakeWord")
    psg = os.path.join(work, "piper-sample-generator")
    if not os.path.exists(oww):
        _run("git clone -q https://github.com/dscripka/openWakeWord", cwd=work)
    if not os.path.exists(psg):
        _run("git clone -q https://github.com/dscripka/piper-sample-generator", cwd=work)
    assert os.path.exists(os.path.join(psg, "generate_samples.py")), (
        "generate_samples.py missing from piper-sample-generator (dscripka fork)"
    )

    # Upstream bug: --convert_to_tflite's argparse default is the *string*
    # "False", which is truthy, so `if args.convert_to_tflite:` fires even
    # when the flag is never passed. We only want the ONNX output, so patch
    # the stray string default to a real boolean.
    train_py = os.path.join(oww, "openwakeword", "train.py")
    with open(train_py) as f:
        content = f.read()
    patched = content.replace(
        'action="store_true",\n        default="False",',
        'action="store_true",\n        default=False,',
    )
    assert patched != content, "convert_to_tflite default string not found to patch"
    with open(train_py, "w") as f:
        f.write(patched)
    return oww, psg


def _download_piper_voice(psg: str):
    """generate_samples.py's `model` default is a hardcoded relative path:
    models/en-us-libritts-high.pt. That filename only exists on the v1.0.0
    release tag (not v2.0.0, which only has the *_r-medium voices)."""
    import os
    import urllib.request

    models_dir = os.path.join(psg, "models")
    os.makedirs(models_dir, exist_ok=True)
    target = os.path.join(models_dir, "en-us-libritts-high.pt")
    if not os.path.exists(target):
        url = (
            "https://github.com/rhasspy/piper-sample-generator/releases/"
            "download/v1.0.0/en-us-libritts-high.pt"
        )
        urllib.request.urlretrieve(url, target)
    return target


def _download_oww_feature_models():
    """AudioFeatures needs melspectrogram.onnx + embedding_model.onnx, which
    aren't bundled in the pip wheel and must be fetched from GitHub release
    assets. Pass a name that won't match anything to skip the (large,
    unneeded) pretrained-wakeword-model download branch."""
    from openwakeword.utils import download_models

    download_models(model_names=["_none_"])


def _download_training_data(work: str):
    """RIRs + FMA backgrounds + ACAV100M negative features + validation set."""
    import os

    import numpy as np
    import scipy.io.wavfile
    from datasets import Audio, load_dataset
    from huggingface_hub import hf_hub_download
    from tqdm import tqdm

    # MIT room impulse responses (reverb augmentation)
    rir_dir = os.path.join(work, "mit_rirs")
    if not os.path.exists(rir_dir):
        os.makedirs(rir_dir)
        ds = load_dataset(
            "davidscripka/MIT_environmental_impulse_responses",
            split="train",
            streaming=True,
        ).cast_column("audio", Audio(sampling_rate=16000))
        for row in tqdm(ds, desc="MIT RIRs"):
            name = row["audio"]["path"].split("/")[-1].replace(".mp3", ".wav")
            scipy.io.wavfile.write(
                os.path.join(rir_dir, name),
                16000,
                (row["audio"]["array"] * 32767).astype(np.int16),
            )

    # FMA music — ~1 hour of background audio (negatives for augmentation)
    fma_dir = os.path.join(work, "fma")
    if not os.path.exists(fma_dir):
        os.makedirs(fma_dir)
        ds = load_dataset(
            "rudraml/fma", name="small", split="train", streaming=True
        ).cast_column("audio", Audio(sampling_rate=16000))
        n = 0
        for row in tqdm(ds, desc="FMA music"):
            scipy.io.wavfile.write(
                os.path.join(fma_dir, f"fma_{n:05d}.wav"),
                16000,
                (row["audio"]["array"] * 32767).astype(np.int16),
            )
            n += 1
            if n >= 200:  # ~1h of clips; enough background variety
                break

    # ACAV100M precomputed negative features (~2GB) + false-positive validation set
    for fn in (
        "openwakeword_features_ACAV100M_2000_hrs_16bit.npy",
        "validation_set_features.npy",
    ):
        target = os.path.join(work, fn)
        if not os.path.exists(target):
            hf_hub_download(
                repo_id="davidscripka/openwakeword_features",
                filename=fn,
                repo_type="dataset",
                local_dir=work,
            )


def _write_config(work: str, oww: str, psg: str, phrase: str, n_samples: int,
                  n_samples_val: int, steps: int, piper_pt: str,
                  target_fp_per_hour: float, max_negative_weight: int,
                  augmentation_rounds: int, target_recall: float,
                  room_ambient_dir: "str | None" = None,
                  room_ambient_weight: int = 3) -> str:
    import os

    import yaml

    base = os.path.join(oww, "examples", "custom_model.yml")
    config = yaml.load(open(base).read(), yaml.Loader)

    model_name = phrase.replace(" ", "_")
    config["target_phrase"] = [phrase]
    config["model_name"] = model_name
    config["n_samples"] = n_samples
    config["n_samples_val"] = n_samples_val
    config["steps"] = steps
    config["target_accuracy"] = 0.7
    config["target_recall"] = target_recall
    # Library defaults (target_false_positives_per_hour=0.2, max_negative_weight=1500)
    # over-suppress false positives at recall's expense. Loosen both so the
    # auto-tuning process stops trading recall away.
    config["target_false_positives_per_hour"] = target_fp_per_hour
    config["max_negative_weight"] = max_negative_weight
    config["augmentation_rounds"] = augmentation_rounds
    # FMA music is generic; the room ambient is this kitchen, this mic, these
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
        work, "validation_set_features.npy"
    )
    config["feature_data_files"] = {
        "ACAV100M_sample": os.path.join(
            work, "openwakeword_features_ACAV100M_2000_hrs_16bit.npy"
        )
    }
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
    import os

    candidates = glob.glob(os.path.join(work, "my_custom_model", "**", "*.onnx"),
                           recursive=True)
    if not candidates:
        candidates = glob.glob(os.path.join(work, "**", "hey_bender*.onnx"),
                               recursive=True)
    if not candidates:
        raise RuntimeError(f"no .onnx produced under {work}/my_custom_model")
    # Prefer the phrase-named model over any intermediate exports.
    candidates.sort(key=lambda p: ("hey_bender" not in os.path.basename(p), len(p)))
    return candidates[0]


def _upload_to_hub(onnx_path: str, token: str, output_name: str = OUTPUT_ONNX_NAME):
    from huggingface_hub import HfApi

    api = HfApi(token=token)
    api.create_repo(repo_id=HF_REPO, repo_type="model", exist_ok=True, private=False)
    api.upload_file(
        path_or_fileobj=onnx_path,
        path_in_repo=output_name,
        repo_id=HF_REPO,
        repo_type="model",
        commit_message=f"Upload {output_name}",
    )
    url = f"https://huggingface.co/{HF_REPO}/blob/main/{output_name}"
    print(f"\nUploaded -> {url}")
    return url


# ---------------------------------------------------------------------------
# The Modal entrypoint function (runs on a T4 GPU).
# ---------------------------------------------------------------------------
@app.function(
    image=image,
    gpu="T4",
    timeout=18000,
    secrets=[modal.Secret.from_name("huggingface")],
    volumes={REAL_SAMPLES_MOUNT: modal.Volume.from_name(
        REAL_SAMPLES_VOLUME, create_if_missing=True)},
)
def train(
    phrase: str = "hey bender",
    n_samples: int = 5000,
    n_samples_val: int = 1000,
    steps: int = 20000,
    target_fp_per_hour: float = 1.0,
    max_negative_weight: int = 500,
    augmentation_rounds: int = 2,
    target_recall: float = 0.5,
    output_name: str = OUTPUT_ONNX_NAME,
    use_real_samples: bool = False,
    real_positive_fraction: float = 0.20,
    real_negative_copies: int = 25,
    room_ambient_weight: int = 3,
):
    import os
    import sys

    token = os.environ.get("HUGGINGFACE_TOKEN") or os.environ.get("HF_TOKEN")
    if not token:
        raise RuntimeError(
            "HUGGINGFACE_TOKEN not set. Create it with: "
            "modal secret create huggingface HUGGINGFACE_TOKEN=hf_xxx"
        )
    os.environ["HF_TOKEN"] = token  # for datasets/hub auth

    work = "/root/work"
    os.makedirs(work, exist_ok=True)

    print("=== 1/5 Clone repos ===")
    oww, psg = _clone_repos(work)
    sys.path.insert(0, psg)  # train.py imports generate_samples from here

    print("=== 2/5 Download piper voice ===")
    piper_pt = _download_piper_voice(psg)

    print("=== 3/5 Download training data ===")
    _download_training_data(work)
    _download_oww_feature_models()

    print("=== 4/5 Write config + train ===")
    # train.py derives these paths from output_dir + model_name; mirror them so
    # real clips land in the same directories --generate_clips will count.
    out_dir = os.path.join(work, "my_custom_model")
    model_name = phrase.replace(" ", "_")
    positive_train_dir = os.path.join(out_dir, model_name, "positive_train")
    negative_train_dir = os.path.join(out_dir, model_name, "negative_train")

    room_ambient_dir = None
    if use_real_samples:
        print("--- seed real-voice clips ---", flush=True)
        split = _load_split(REAL_SAMPLES_MOUNT)
        _seed_real_clips(REAL_SAMPLES_MOUNT, split, positive_train_dir,
                         negative_train_dir, n_samples, real_positive_fraction,
                         real_negative_copies)
        _seed_conversation_negatives(REAL_SAMPLES_MOUNT, split, negative_train_dir)
        room_ambient_dir = _ambient_background_dir(REAL_SAMPLES_MOUNT, split, work)
    else:
        print("No real samples requested: synthetic-only run (the v0.1 recipe).")

    cfg = _write_config(work, oww, psg, phrase, n_samples, n_samples_val, steps, piper_pt,
                        target_fp_per_hour, max_negative_weight, augmentation_rounds,
                        target_recall, room_ambient_dir, room_ambient_weight)
    train_py = os.path.join(oww, "openwakeword", "train.py")
    env = f'PYTHONPATH="{psg}:$PYTHONPATH"'
    for label, flag in (
        ("generate clips", "--generate_clips"),
        ("augment clips", "--augment_clips"),
        ("train model", "--train_model"),
    ):
        print(f"--- {label} ---", flush=True)
        _run(f'{env} python "{train_py}" --training_config "{cfg}" {flag}', cwd=work)

    print("=== 5/5 Upload to HF Hub ===")
    onnx = _find_onnx(work)
    print(f"Found model: {onnx}")
    return _upload_to_hub(onnx, token, output_name)


@app.local_entrypoint()
def main(
    phrase: str = "hey bender",
    n_samples: int = 5000,
    n_samples_val: int = 1000,
    steps: int = 20000,
    target_fp_per_hour: float = 1.0,
    max_negative_weight: int = 500,
    augmentation_rounds: int = 2,
    target_recall: float = 0.5,
    use_real_samples: bool = False,
    real_positive_fraction: float = 0.20,
    real_negative_copies: int = 25,
    room_ambient_weight: int = 3,
    output_name: str = OUTPUT_ONNX_NAME,
):
    url = train.remote(
        phrase=phrase,
        n_samples=n_samples,
        n_samples_val=n_samples_val,
        steps=steps,
        target_fp_per_hour=target_fp_per_hour,
        max_negative_weight=max_negative_weight,
        augmentation_rounds=augmentation_rounds,
        target_recall=target_recall,
        use_real_samples=use_real_samples,
        real_positive_fraction=real_positive_fraction,
        real_negative_copies=real_negative_copies,
        room_ambient_weight=room_ambient_weight,
        output_name=output_name,
    )
    print("\nDone. Model URL:")
    print(url)
    print("\nDeploy on the Pi with: bash scripts/deploy_hey_bender.sh")


# Sweep grid: (n_samples, augmentation_rounds, target_recall). Kept small
# (default 6 combos) so a full sweep is a few dollars of T4 time. Each combo
# uploads a grid-tagged ONNX so nothing overwrites the canonical v0.1 model.
_SWEEP_GRID = [
    (n, aug, rec)
    for n in (5000, 15000)
    for aug in (1, 2)
    for rec in (0.5,)
] + [
    (15000, 2, 0.7),
    (25000, 2, 0.6),
]


@app.local_entrypoint()
def sweep(
    phrase: str = "hey bender",
    steps: int = 20000,
    n_samples_val: int = 1000,
    target_fp_per_hour: float = 1.0,
    max_negative_weight: int = 500,
):
    """Run a parallel grid sweep over (n_samples, augmentation_rounds,
    target_recall) on Modal T4s and print a ranking table of the uploaded
    models.

    NOTE: openWakeWord's synthetic validation metrics do NOT perfectly predict
    real-mic recall — treat the table as a *ranking* to pick candidates for
    live on-BenderPi testing, not as ground truth. Winner selection MUST be
    confirmed by saying "hey bender" at distance/volume variations on the Pi.

    Run: modal run scripts/train_hey_bender.py::sweep
    """
    combos = list(_SWEEP_GRID)
    print(f"Sweeping {len(combos)} combos (n_samples x aug_rounds x target_recall):")
    for n, aug, rec in combos:
        print(f"  n={n:>6}  aug={aug}  recall={rec}")

    def _args(combo):
        n, aug, rec = combo
        tag = f"hey_bender_n{n}_aug{aug}_rec{str(rec).replace('.', 'p')}.onnx"
        return (
            phrase, n, n_samples_val, steps,
            target_fp_per_hour, max_negative_weight, aug, rec, tag,
        )

    starmap_args = [_args(c) for c in combos]
    results = []
    for combo, url in zip(combos, train.starmap(starmap_args)):
        n, aug, rec = combo
        results.append((n, aug, rec, url))

    print("\n=== Sweep results ===")
    print(f"{'n_samples':>10} {'aug':>4} {'recall':>7}  model_url")
    for n, aug, rec, url in results:
        print(f"{n:>10} {aug:>4} {rec:>7}  {url}")
    print("\nPick a candidate from the ranking, deploy it to the Pi, and confirm "
          "recall/precision LIVE by saying 'hey bender' at distance/volume "
          "variations. Synthetic metrics rank; the mic decides.")


# Ratio sweep for v0.2: the share of the positive set that is real voice is the
# one knob the plan cannot settle by argument. 10% under-weights the real
# clips (that is v0.1's failure mode); 35% risks memorising 80 recordings.
# The held-out set decides. Each run uploads its own tagged ONNX.
_REAL_FRACTIONS = (0.10, 0.20, 0.35)


@app.local_entrypoint()
def sweep_real(
    phrase: str = "hey bender",
    n_samples: int = 20000,
    n_samples_val: int = 1000,
    steps: int = 50000,
    target_fp_per_hour: float = 1.0,
    max_negative_weight: int = 500,
    augmentation_rounds: int = 2,
    target_recall: float = 0.5,
    real_negative_copies: int = 25,
    room_ambient_weight: int = 3,
):
    """Train one model per real-clip fraction, in parallel.

    Run: modal run scripts/train_hey_bender.py::sweep_real

    Then score every candidate on the DEVICE against the held-out clips:
        venv/bin/python scripts/eval_wake_model.py --model models/<name>.onnx
    Synthetic validation metrics rank candidates; the held-out real clips
    decide, because the whole defect is a synthetic-to-real gap.
    """
    args = [
        (phrase, n_samples, n_samples_val, steps, target_fp_per_hour,
         max_negative_weight, augmentation_rounds, target_recall,
         f"hey_bender_v0.2_r{int(f * 100):02d}.onnx",
         True, f, real_negative_copies, room_ambient_weight)
        for f in _REAL_FRACTIONS
    ]
    print(f"Sweeping real-clip fractions: {', '.join(f'{f:.0%}' for f in _REAL_FRACTIONS)}")
    for f, url in zip(_REAL_FRACTIONS, train.starmap(args)):
        print(f"  real={f:.0%}  {url}")
    print("\nNow evaluate each on BenderPi with scripts/eval_wake_model.py "
          "(held-out clips only) and ship against the gates in the plan.")
