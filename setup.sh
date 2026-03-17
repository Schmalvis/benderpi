#!/usr/bin/env bash
# setup.sh — BenderPi one-shot installer
#
# Run this on a fresh Raspberry Pi OS (64-bit) after completing the
# hardware prerequisites documented in README.md (seeed-voicecard driver,
# SPI enabled, ALSA config).
#
# Usage:
#   git clone git@github.com:Schmalvis/benderpi.git
#   cd benderpi
#   ./setup.sh

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIPER_VERSION="2023.11.14-2"
PIPER_URL="https://github.com/rhasspy/piper/releases/download/${PIPER_VERSION}/piper_linux_aarch64.tar.gz"
HF_ASSETS_REPO="Schmalvis/benderpi-assets"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()    { echo -e "${GREEN}[setup]${NC} $*"; }
warn()    { echo -e "${YELLOW}[warn]${NC}  $*"; }
error()   { echo -e "${RED}[error]${NC} $*"; exit 1; }
prompt()  { echo -e "${YELLOW}[input]${NC} $*"; }

# ── Preflight ──────────────────────────────────────────────────────────────

info "BenderPi setup starting..."

[[ "$(uname -m)" == "aarch64" ]] || error "BenderPi requires aarch64 (Raspberry Pi). Got: $(uname -m)"
[[ "$(uname -s)" == "Linux" ]]   || error "BenderPi requires Linux."

# Check Python 3.11+
PYTHON=$(command -v python3 2>/dev/null || true)
[[ -n "$PYTHON" ]] || error "python3 not found — install it first: sudo apt install python3"
PY_VER=$($PYTHON -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
[[ "$PY_MAJOR" -ge 3 && "$PY_MINOR" -ge 11 ]] || error "Python 3.11+ required. Found: $PY_VER"
info "Python $PY_VER ✓"

# Check seeed-voicecard is installed (ALSA card 2 = seeed-2mic-voicecard)
if ! aplay -l 2>/dev/null | grep -q "seeed\|wm8960\|2mic"; then
    warn "seeed-voicecard driver not detected."
    warn "Install it before running setup — see README.md → Hardware Prerequisites."
    warn "Continuing anyway; audio will not work until the driver is installed."
fi

# Check SPI is enabled
if ! ls /dev/spidev* &>/dev/null; then
    warn "SPI device not found. Add 'dtparam=spi=on' to /boot/firmware/config.txt and reboot."
    warn "Continuing anyway; LEDs will not work until SPI is enabled."
fi

# ── System packages ────────────────────────────────────────────────────────

info "Installing system packages..."
sudo apt-get update -qq
sudo apt-get install -y -qq \
    python3-dev \
    python3-venv \
    python3-lgpio \
    libportaudio2 \
    portaudio19-dev \
    libsndfile1 \
    espeak-ng \
    curl \
    ffmpeg \
    git

# ── Python venv ────────────────────────────────────────────────────────────

VENV_DIR="$REPO_DIR/venv"
info "Creating Python venv at $VENV_DIR..."
# --system-site-packages required for hardware libs (lgpio, adafruit-blinka, neopixel)
# installed system-wide via apt / Pi OS
python3 -m venv --system-site-packages "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade -q pip wheel
info "Installing Python dependencies..."
"$VENV_DIR/bin/pip" install -q -r "$REPO_DIR/requirements.txt"
info "Python dependencies installed ✓"

# ── Piper TTS binary ───────────────────────────────────────────────────────

PIPER_DIR="$REPO_DIR/piper"
if [[ -x "$PIPER_DIR/piper" ]]; then
    info "Piper binary already present ✓"
else
    info "Downloading Piper $PIPER_VERSION (aarch64)..."
    mkdir -p "$PIPER_DIR"
    curl -fsSL "$PIPER_URL" | tar xz -C "$PIPER_DIR" --strip-components=1
    chmod +x "$PIPER_DIR/piper"
    info "Piper downloaded ✓"
fi

# ── Assets from Hugging Face Hub ───────────────────────────────────────────

MODELS_DIR="$REPO_DIR/models"
WAV_DIR="$REPO_DIR/speech/wav"

NEED_MODEL=false
NEED_CLIPS=false
[[ -f "$MODELS_DIR/bender.onnx" && -f "$MODELS_DIR/bender.onnx.json" ]] || NEED_MODEL=true
[[ -n "$(ls "$WAV_DIR"/*.wav 2>/dev/null)" ]] || NEED_CLIPS=true

if $NEED_MODEL || $NEED_CLIPS; then
    echo ""
    echo "────────────────────────────────────────────────────────────"
    echo " Hugging Face Hub assets"
    echo "────────────────────────────────────────────────────────────"
    echo " The Bender voice model and audio clips are stored in a"
    echo " private HF Hub repo (${HF_ASSETS_REPO})."
    echo ""
    echo " You need a Hugging Face token with access to that repo."
    echo " Get one at: https://huggingface.co/settings/tokens"
    echo ""
    prompt "Enter your HF token (or press Enter to skip):"
    read -r -s HF_TOKEN
    echo ""

    if [[ -n "$HF_TOKEN" ]]; then
        info "Downloading assets from HF Hub..."
        mkdir -p "$MODELS_DIR" "$WAV_DIR"

        "$VENV_DIR/bin/python3" - <<PYEOF
import os, sys
os.environ["HF_TOKEN"] = "$HF_TOKEN"
from huggingface_hub import snapshot_download
try:
    snapshot_download(
        repo_id="$HF_ASSETS_REPO",
        repo_type="dataset",
        local_dir="/tmp/benderpi-assets",
        token="$HF_TOKEN",
    )
    import shutil, glob
    # Copy model files
    for f in glob.glob("/tmp/benderpi-assets/models/*"):
        shutil.copy2(f, "$MODELS_DIR/")
    # Copy wav clips
    for f in glob.glob("/tmp/benderpi-assets/speech/wav/*"):
        shutil.copy2(f, "$WAV_DIR/")
    print("Assets downloaded ✓")
except Exception as e:
    print(f"Download failed: {e}", file=sys.stderr)
    sys.exit(1)
PYEOF
    else
        warn "Skipping asset download."
        warn "You'll need to manually place:"
        warn "  - models/bender.onnx and models/bender.onnx.json"
        warn "  - speech/wav/*.wav (Bender audio clips)"
        warn "Or train your own Piper model — see README.md → Voice Model."
    fi
else
    info "Assets already present ✓"
fi

# ── .env configuration ─────────────────────────────────────────────────────

ENV_FILE="$REPO_DIR/.env"
if [[ -f "$ENV_FILE" ]]; then
    info ".env already exists — leaving it unchanged."
else
    info "Creating .env from template..."
    cp "$REPO_DIR/.env.example" "$ENV_FILE"

    echo ""
    echo "────────────────────────────────────────────────────────────"
    echo " Configuration"
    echo "────────────────────────────────────────────────────────────"

    prompt "Picovoice access key (get one free at console.picovoice.ai):"
    read -r PORCUPINE_KEY
    sed -i "s|^PORCUPINE_ACCESS_KEY=.*|PORCUPINE_ACCESS_KEY=${PORCUPINE_KEY}|" "$ENV_FILE"

    prompt "Anthropic API key (claude.ai → API keys):"
    read -r ANTHROPIC_KEY
    sed -i "s|^ANTHROPIC_API_KEY=.*|ANTHROPIC_API_KEY=${ANTHROPIC_KEY}|" "$ENV_FILE"

    prompt "Home Assistant URL [http://homeassistant.local:8123]:"
    read -r HA_URL
    HA_URL="${HA_URL:-http://homeassistant.local:8123}"
    sed -i "s|^HA_URL=.*|HA_URL=${HA_URL}|" "$ENV_FILE"

    prompt "Home Assistant long-lived access token:"
    read -r -s HA_TOKEN
    echo ""
    sed -i "s|^HA_TOKEN=.*|HA_TOKEN=${HA_TOKEN}|" "$ENV_FILE"

    prompt "Web UI PIN [choose something other than 1234]:"
    read -r WEB_PIN
    WEB_PIN="${WEB_PIN:-1234}"
    sed -i "s|^BENDER_WEB_PIN=.*|BENDER_WEB_PIN=${WEB_PIN}|" "$ENV_FILE"

    info ".env written ✓"
fi

# ── Pre-build response library ─────────────────────────────────────────────

info "Pre-building TTS response library..."
if [[ -f "$MODELS_DIR/bender.onnx" ]]; then
    cd "$REPO_DIR"
    "$VENV_DIR/bin/python3" scripts/prebuild_responses.py
    info "Response library built ✓"
else
    warn "Skipping response pre-build (model not present)."
fi

# ── TLS certificate (for HTTPS web UI) ────────────────────────────────────

TLS_DIR="$REPO_DIR/tls"
if [[ -f "$TLS_DIR/cert.pem" && -f "$TLS_DIR/key.pem" ]]; then
    info "TLS certificate already present ✓"
else
    info "Generating self-signed TLS certificate..."
    mkdir -p "$TLS_DIR"
    openssl req -x509 -newkey rsa:2048 -keyout "$TLS_DIR/key.pem" -out "$TLS_DIR/cert.pem" -days 3650 -nodes -subj "/CN=benderpi" 2>/dev/null
    info "TLS certificate generated ✓  (accept the browser warning once)"
fi

# ── Systemd services ───────────────────────────────────────────────────────

info "Installing systemd services..."
sudo cp "$REPO_DIR/systemd/bender-converse.service" /etc/systemd/system/
sudo cp "$REPO_DIR/systemd/bender-web.service"      /etc/systemd/system/

# Install git-pull timer if present
if [[ -f "$REPO_DIR/systemd/bender-git-pull.service" ]]; then
    sudo cp "$REPO_DIR/systemd/bender-git-pull.service" /etc/systemd/system/
    sudo cp "$REPO_DIR/systemd/bender-git-pull.timer"   /etc/systemd/system/
fi

sudo systemctl daemon-reload
sudo systemctl enable bender-converse bender-web
[[ -f /etc/systemd/system/bender-git-pull.timer ]] && \
    sudo systemctl enable bender-git-pull.timer

info "Services installed and enabled ✓"

# ── sudoers ────────────────────────────────────────────────────────────────

SUDOERS_FILE="/etc/sudoers.d/bender-web"
if [[ ! -f "$SUDOERS_FILE" ]]; then
    info "Installing sudoers rules for web UI..."
    sudo tee "$SUDOERS_FILE" > /dev/null <<'EOF'
pi ALL=(ALL) NOPASSWD: /bin/systemctl restart bender-converse
pi ALL=(ALL) NOPASSWD: /bin/systemctl stop bender-converse
pi ALL=(ALL) NOPASSWD: /bin/systemctl start bender-converse
pi ALL=(ALL) NOPASSWD: /bin/systemctl restart bender-web
pi ALL=(ALL) NOPASSWD: /bin/systemctl status bender-converse
EOF
    sudo chmod 440 "$SUDOERS_FILE"
    info "sudoers installed ✓"
else
    info "sudoers already present ✓"
fi

# ── Done ───────────────────────────────────────────────────────────────────

echo ""
echo "════════════════════════════════════════════════════════════"
echo -e " ${GREEN}BenderPi setup complete!${NC}"
echo "════════════════════════════════════════════════════════════"
echo ""
echo " Start services:"
echo "   sudo systemctl start bender-converse"
echo "   sudo systemctl start bender-web"
echo ""
echo " Web UI: https://$(hostname -I | awk '{print $1}'):8080"
echo ""
echo " Check logs:"
echo "   journalctl -u bender-converse -f"
echo "   journalctl -u bender-web -f"
echo ""
if [[ ! -f "$MODELS_DIR/bender.onnx" ]]; then
    echo -e " ${YELLOW}⚠ Voice model not installed — add models/bender.onnx before starting.${NC}"
    echo ""
fi
