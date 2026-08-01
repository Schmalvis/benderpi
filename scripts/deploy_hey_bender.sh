#!/usr/bin/env bash
#
# deploy_hey_bender.sh — pull the trained "hey bender" openWakeWord model from
# the HF Hub and switch BenderPi over to it.
#
# Run on the Pi (in /home/pi/bender):
#   bash scripts/deploy_hey_bender.sh
#
# What it does:
#   1. Downloads hey_bender_v0.1.onnx from Schmalvis/hey-bender-oww into models/
#   2. Points oww_model_path in bender_config.json at the new model
#   3. Restarts bender-converse
#
set -euo pipefail

REPO="Schmalvis/hey-bender-oww"
MODEL_FILE="hey_bender_v0.1.onnx"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODELS_DIR="${PROJECT_DIR}/models"
CONFIG="${PROJECT_DIR}/bender_config.json"
MODEL_PATH="models/${MODEL_FILE}"
PY="${PROJECT_DIR}/venv/bin/python"

mkdir -p "${MODELS_DIR}"

echo "[1/3] Downloading ${MODEL_FILE} from ${REPO} ..."
"${PY}" - "$REPO" "$MODEL_FILE" "$MODELS_DIR" <<'PYEOF'
import sys
from huggingface_hub import hf_hub_download
repo, fname, dest = sys.argv[1], sys.argv[2], sys.argv[3]
path = hf_hub_download(repo_id=repo, filename=fname, local_dir=dest)
print(f"  saved -> {path}")
PYEOF

echo "[2/3] Updating oww_model_path in bender_config.json ..."
"${PY}" - "$CONFIG" "$MODEL_PATH" <<'PYEOF'
import json, sys
cfg_path, model_path = sys.argv[1], sys.argv[2]
with open(cfg_path) as f:
    cfg = json.load(f)
cfg["oww_model_path"] = model_path
with open(cfg_path, "w") as f:
    json.dump(cfg, f, indent=2)
    f.write("\n")
print(f"  oww_model_path -> {model_path}")
PYEOF

echo "[3/3] Restarting bender-converse ..."
sudo systemctl restart bender-converse

echo "Done. oww_threshold is 0.1 in bender_config.json: this model was trained on"
echo "synthetic positives and scores ~0.25 on real voices (0.97 on synthetic), while"
echo "non-wake audio stays under 0.03. Raise it if you get phantom wakes; if it stops"
echo "firing for you, score your own voice before raising it further."
echo "Tail logs with: sudo journalctl -u bender-converse -f"
