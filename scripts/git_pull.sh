#!/bin/bash
# Auto-pull latest changes from GitHub for BenderPi
# Runs via systemd timer every 5 minutes

set -e

REPO_DIR="/home/pi/bender"
SERVICE="bender-converse"

cd "$REPO_DIR"

# Fetch without merging first to check if there's anything new
git fetch origin main --quiet

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)

if [ "$LOCAL" = "$REMOTE" ]; then
    echo "Already up to date ($LOCAL)"
    exit 0
fi

echo "New commits available: $LOCAL -> $REMOTE"
echo "Changes:"
git log --oneline HEAD..origin/main

# Pull
git pull origin main --ff-only

echo "Pull complete. Restarting $SERVICE..."
sudo systemctl restart "$SERVICE"
echo "Generating status report..."
"$REPO_DIR/venv/bin/python" "$REPO_DIR/scripts/generate_status.py" || true
echo "Done."
