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
                  augmentation_rounds: int, target_recall: float) -> str:
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
    config["background_paths"] = [os.path.join(work, "fma")]
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
    cfg = _write_config(work, oww, psg, phrase, n_samples, n_samples_val, steps, piper_pt,
                        target_fp_per_hour, max_negative_weight, augmentation_rounds,
                        target_recall)
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
