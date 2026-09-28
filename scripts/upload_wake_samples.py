#!/usr/bin/env python3
"""Upload the captured wake-word samples to a PRIVATE Hugging Face dataset.

The training job runs on Hugging Face infrastructure, so the clips have to be
reachable from there. This is household audio -- family conversation, the
television, whatever was happening in the room -- so the repo is created
private and this script refuses to touch a public one. It is also the reason
`data/wake_samples/` is gitignored and has a test pinning that.

Run it from the device, where the samples live:
    venv/bin/python scripts/upload_wake_samples.py
    venv/bin/python scripts/upload_wake_samples.py --dry-run
    venv/bin/python scripts/upload_wake_samples.py --repo someone/other-name

Needs a write token: `hf auth login`, or HF_TOKEN in the environment.

The split file goes up with the clips, so the training job reads the same
frozen division the device evaluates against. Re-run this after any capture
session, and after re-running split_wake_samples.py.
"""

import argparse
import glob
import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLES_DIR = os.path.join(BASE_DIR, "data", "wake_samples")
SPLIT_PATH = os.path.join(SAMPLES_DIR, "split.json")
DEFAULT_REPO = "Schmalvis/bender-wake-samples"


def summarise(root: str) -> dict:
    """What is about to be uploaded, by mode."""
    out = {}
    for mode in ("positive", "hard_negative"):
        out[mode] = len(glob.glob(os.path.join(root, mode, "*", "*.wav")))
    for mode in ("ambient", "conversation"):
        out[mode] = len(glob.glob(os.path.join(root, mode, "*.wav")))
    out["bytes"] = sum(os.path.getsize(f)
                       for f in glob.glob(os.path.join(root, "**", "*.wav"),
                                          recursive=True))
    return out


def check_split(path: str) -> dict:
    """The job refuses to train without this, so fail here where it is cheap."""
    if not os.path.exists(path):
        raise SystemExit(
            f"{path} missing — run scripts/split_wake_samples.py first. "
            "Uploading clips without the frozen split would let a training run "
            "use the held-out ones.")
    split = json.load(open(path))
    for key in ("positive", "hard_negative", "ambient"):
        if key not in split:
            raise SystemExit(f"split.json has no '{key}' section; regenerate it")
    if not split["positive"]["holdout"]:
        raise SystemExit("split.json holds out no positives — nothing to measure with")
    return split


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not os.path.isdir(SAMPLES_DIR):
        raise SystemExit(f"{SAMPLES_DIR} not found — capture samples first "
                         "(scripts/capture_wake_samples.py), on the device")
    split = check_split(SPLIT_PATH)
    counts = summarise(SAMPLES_DIR)

    print(f"Uploading to {args.repo} (private dataset)")
    print(f"  positives       {counts['positive']:4d}  "
          f"({len(split['positive']['train'])} train / "
          f"{len(split['positive']['holdout'])} held out)")
    print(f"  hard negatives  {counts['hard_negative']:4d}  "
          f"({len(split['hard_negative']['train'])} train / "
          f"{len(split['hard_negative']['holdout'])} held out / "
          f"{len(split['hard_negative'].get('watch', []))} watch)")
    print(f"  ambient         {counts['ambient']:4d} min")
    print(f"  conversation    {counts['conversation']:4d} min")
    print(f"  total           {counts['bytes'] / 1e6:.0f} MB")

    if args.dry_run:
        print("\n--dry-run: nothing uploaded.")
        return

    from huggingface_hub import HfApi
    api = HfApi()
    try:
        api.whoami()
    except Exception:
        raise SystemExit("not authenticated — run `hf auth login` with a write token")

    api.create_repo(repo_id=args.repo, repo_type="dataset",
                    private=True, exist_ok=True)
    info = api.repo_info(repo_id=args.repo, repo_type="dataset")
    if not getattr(info, "private", True):
        raise SystemExit(
            f"{args.repo} is PUBLIC. This is household audio; make it private "
            "in the repo settings, or pass --repo with a different name. "
            "Refusing to upload.")

    api.upload_folder(repo_id=args.repo, repo_type="dataset",
                      folder_path=SAMPLES_DIR, path_in_repo="",
                      commit_message="wake-word samples + frozen split")
    print(f"\nDone: https://huggingface.co/datasets/{args.repo} (private)")
    print("Train with:")
    print("  hf jobs uv run --flavor t4-small --timeout 5h --secrets HF_TOKEN \\")
    print("      scripts/train_hey_bender_hf.py -- --use-real-samples \\")
    print("      --real-positive-fraction 0.20 --output-name hey_bender_v0.2_r20.onnx")


if __name__ == "__main__":
    main()
