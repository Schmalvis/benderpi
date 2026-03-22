#!/bin/bash
# Auto-pull latest changes from GitHub for BenderPi
# Runs via systemd timer every 5 minutes

set -e

REPO_DIR="/home/pi/bender"
SERVICE="bender-converse"

cd "$REPO_DIR"

# Fetch without merging first to check if there's anything new
git fetch origin main --quiet

BEHIND=$(git rev-list HEAD..origin/main --count)
if [ "$BEHIND" = "0" ]; then
    echo "Nothing to pull (up to date or local is ahead of remote)"
    exit 0
fi

echo "$BEHIND new commit(s) available on remote:"
git log --oneline HEAD..origin/main

# Pull
git pull origin main --ff-only

echo "Pull complete. Restarting $SERVICE..."
sudo systemctl restart "$SERVICE"
echo "Generating status report..."
"$REPO_DIR/venv/bin/python" "$REPO_DIR/scripts/generate_status.py" || true

# Restart web UI if running
if systemctl is-active --quiet bender-web; then
    sudo systemctl restart bender-web
    echo "Web UI restarted."
fi

echo "Done."
