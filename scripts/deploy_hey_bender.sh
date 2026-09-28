#!/usr/bin/env bash
#
# deploy_hey_bender.sh — pull a trained "hey bender" openWakeWord model from
# the HF Hub and switch BenderPi over to it.
#
# Run on the Pi (in /home/pi/bender):
#   bash scripts/deploy_hey_bender.sh                          # re-deploy the default
#   bash scripts/deploy_hey_bender.sh hey_bender_v0.2_r20.onnx --threshold 0.35
#   bash scripts/deploy_hey_bender.sh <model> --no-eval        # skip the gate check
#   bash scripts/deploy_hey_bender.sh <model> --force          # deploy a failing model
#
# What it does:
#   1. Downloads the model from Schmalvis/hey-bender-oww into models/
#   2. Scores it against the HELD-OUT clips and refuses to switch if it fails
#      the ship gates (scripts/eval_wake_model.py; --no-eval to skip)
#   3. Points oww_model_path (and optionally oww_threshold) at it
#   4. Restarts bender-converse, printing the exact rollback command first
#
# Why the gate check is on by default: v0.1 scores 0.966 on a synthetic
# "hey bender" and wakes on 2 of 26 held-out real ones. Any candidate can look
# good on its own training metrics, so the only check worth blocking on is
# recall on clips the training run never saw.
#
set -euo pipefail

REPO="Schmalvis/hey-bender-oww"
MODEL_FILE="hey_bender_v0.1.onnx"
THRESHOLD=""
RUN_EVAL=1
FORCE=0

while [ $# -gt 0 ]; do
    case "$1" in
        --threshold) THRESHOLD="${2:?--threshold needs a value}"; shift 2 ;;
        --no-eval)   RUN_EVAL=0; shift ;;
        --force)     FORCE=1; shift ;;
        --repo)      REPO="${2:?--repo needs a value}"; shift 2 ;;
        -h|--help)   sed -n '2,25p' "$0"; exit 0 ;;
        -*)          echo "unknown option: $1" >&2; exit 2 ;;
        *)           MODEL_FILE="$1"; shift ;;
    esac
done

case "$MODEL_FILE" in
    */*|"")  echo "model must be a bare filename, not a path: $MODEL_FILE" >&2; exit 2 ;;
    *.onnx|*.tflite) ;;
    *)       echo "model must be .onnx or .tflite: $MODEL_FILE" >&2; exit 2 ;;
esac

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODELS_DIR="${PROJECT_DIR}/models"
CONFIG="${PROJECT_DIR}/bender_config.json"
MODEL_PATH="models/${MODEL_FILE}"
PY="${PROJECT_DIR}/venv/bin/python"

mkdir -p "${MODELS_DIR}"

# Print the rollback BEFORE changing anything: the current values are what you
# need to get back, and they are gone the moment step 3 writes the file.
PREV="$("${PY}" - "$CONFIG" <<'PYEOF'
import json, sys
cfg = json.load(open(sys.argv[1]))
print(f"{cfg.get('oww_model_path', '')}\t{cfg.get('oww_threshold', '')}")
PYEOF
)"
PREV_MODEL="${PREV%%$'\t'*}"
PREV_THRESHOLD="${PREV##*$'\t'}"
echo "Current: oww_model_path=${PREV_MODEL}  oww_threshold=${PREV_THRESHOLD}"

echo "[1/4] Downloading ${MODEL_FILE} from ${REPO} ..."
if [ -f "${MODELS_DIR}/${MODEL_FILE}" ]; then
    echo "  already present -> ${MODELS_DIR}/${MODEL_FILE}"
else
    "${PY}" - "$REPO" "$MODEL_FILE" "$MODELS_DIR" <<'PYEOF'
import sys
from huggingface_hub import hf_hub_download
repo, fname, dest = sys.argv[1], sys.argv[2], sys.argv[3]
path = hf_hub_download(repo_id=repo, filename=fname, local_dir=dest)
print(f"  saved -> {path}")
PYEOF
fi

if [ "$RUN_EVAL" = "1" ]; then
    echo "[2/4] Scoring ${MODEL_FILE} against the held-out clips ..."
    GATE_JSON="$(mktemp)"
    trap 'rm -f "$GATE_JSON"' EXIT
    if ! "${PY}" "${PROJECT_DIR}/scripts/eval_wake_model.py" \
            --model "${MODEL_PATH}" --json "${GATE_JSON}" --skip-mic-check; then
        echo "  evaluation failed to run. Fix that before deploying, or pass" >&2
        echo "  --no-eval if you know why it cannot run here." >&2
        exit 1
    fi
    SHIP="$("${PY}" - "$GATE_JSON" <<'PYEOF'
import json, sys
print("yes" if json.load(open(sys.argv[1]))["gates"][0]["ship"] else "no")
PYEOF
)"
    if [ "$SHIP" != "yes" ]; then
        if [ "$FORCE" = "1" ]; then
            echo "  gates FAILED — deploying anyway (--force)."
        else
            echo "" >&2
            echo "  Ship gates FAILED. Not deploying." >&2
            echo "  Re-run the evaluation to see which gate and by how much:" >&2
            echo "    venv/bin/python scripts/eval_wake_model.py --model ${MODEL_PATH}" >&2
            echo "  Deploy anyway with --force, or skip the check with --no-eval." >&2
            exit 1
        fi
    else
        echo "  gates PASSED."
    fi
else
    echo "[2/4] Skipping the held-out evaluation (--no-eval)."
fi

echo "[3/4] Updating bender_config.json ..."
"${PY}" - "$CONFIG" "$MODEL_PATH" "$THRESHOLD" <<'PYEOF'
import json, sys
cfg_path, model_path, threshold = sys.argv[1], sys.argv[2], sys.argv[3]
with open(cfg_path) as f:
    cfg = json.load(f)
cfg["oww_model_path"] = model_path
print(f"  oww_model_path -> {model_path}")
if threshold:
    cfg["oww_threshold"] = float(threshold)
    print(f"  oww_threshold  -> {float(threshold)}")
with open(cfg_path, "w") as f:
    json.dump(cfg, f, indent=2)
    f.write("\n")
PYEOF

echo ""
echo "  Rollback (keep this):"
if [ -n "$THRESHOLD" ]; then
    echo "    bash scripts/deploy_hey_bender.sh $(basename "$PREV_MODEL") --threshold ${PREV_THRESHOLD} --no-eval"
else
    echo "    bash scripts/deploy_hey_bender.sh $(basename "$PREV_MODEL") --no-eval"
fi
echo ""

echo "[4/4] Restarting bender-converse ..."
sudo systemctl restart bender-converse

echo "Done. Confirm it is listening, then use it normally for a day:"
echo "  grep -a 'Listening for wake word' logs/bender.log | tail -1"
echo "  grep -a 'Wake word detected' logs/bender.log | tail -5"
echo "Tail logs with: sudo journalctl -u bender-converse -f"
