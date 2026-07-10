#!/bin/bash
# Auto-pull latest changes from GitHub for BenderPi, with a fail-closed
# rollback guard. Runs via systemd timer every 5 minutes.
#
# Failure/rollback state machine
# -------------------------------
#  1. Capture PREV_SHA before touching anything.
#  2. Fetch origin/main. If its head matches the bad-SHA marker written by a
#     previous failed deploy, skip entirely (don't hammer a known-bad commit
#     every 5 minutes). If the marker exists but the remote has since moved
#     on, clear it — a new commit deserves a fresh attempt.
#  3. Pull --ff-only to NEW_SHA.
#  4. Gate A: py_compile every *added/copied/modified/renamed* .py file
#     between PREV_SHA..NEW_SHA (deleted files are excluded — they no longer
#     exist on disk, so compiling them is a false failure, not a real one).
#  5. Gate B: if requirements.txt changed, pip install -r requirements.txt.
#  6. Gate C: restart bender-converse. The service is Type=notify and only
#     sends READY=1 once the wake loop is actually listening (see
#     wake_converse.py), so a blocking `systemctl restart` returning
#     successfully already proves the wake loop came up — bounded by a
#     `timeout` in case the restart itself wedges (e.g. a blocked USB mic
#     read stuck the stop phase — the real failure mode this guards against).
#  7. Gate D: short sleep + `systemctl is-active --quiet` to catch a
#     crash-immediately-after-ready (READY=1 sent, then died).
#  8. Any gate failure -> rollback: `git reset --hard PREV_SHA`, best-effort
#     `pip install` back to the old requirements, restart on the old commit,
#     write NEW_SHA to the bad-SHA marker, loud "DEPLOY FAILED" lines (grep
#     -friendly in journald), exit 1.
#
# Notes:
#  - Every command that can legitimately fail as part of normal operation
#    (compile, pip, restart, is-active, status generation) is wrapped in an
#    explicit `if ! cmd; then …; fi` / `if cmd; then … else …; fi` gate.
#    Under `set -e`, a failing command used as an `if` condition does NOT
#    trigger the trap — only an *unguarded* bare failing command would abort
#    the script mid-rollback. Keep every fallible command inside an
#    `if`/`&&`/`||` construct; never let one run bare.
#  - `pip install` on rollback does not uninstall packages the bad commit
#    added. That's intentional, not a bug to "fix": the pull that got us here
#    just succeeded, so network access exists, and leftover installed
#    packages are harmless clutter, not a functional risk.
#  - Rollback reuses the exact same `sudo systemctl restart bender-converse`
#    call as the normal path, so it needs no additional sudoers grant.

set -e

# REPO_DIR/SERVICE are overridable via env for the test harness
# (scripts/test_git_pull.sh); on the Pi these always take the defaults.
REPO_DIR="${BENDER_REPO_DIR:-/home/pi/bender}"
SERVICE="${BENDER_SERVICE:-bender-converse}"
BAD_SHA_MARKER="$REPO_DIR/.git_pull_bad_sha"
RESTART_TIMEOUT_S="${BENDER_RESTART_TIMEOUT_S:-90}"
PIP_TIMEOUT_S="${BENDER_PIP_TIMEOUT_S:-120}"
POST_RESTART_SETTLE_S="${BENDER_POST_RESTART_SETTLE_S:-5}"

cd "$REPO_DIR"

PREV_SHA="$(git rev-parse HEAD)"
NEW_SHA="$PREV_SHA"

rollback() {
    local reason="$1"
    echo "DEPLOY FAILED: $reason"
    echo "DEPLOY FAILED: rolling back $PREV_SHA..$NEW_SHA -> $PREV_SHA"
    if ! git reset --hard "$PREV_SHA"; then
        echo "DEPLOY FAILED: git reset --hard $PREV_SHA also failed — repo may be in a bad state, manual intervention required"
        exit 1
    fi
    # Best-effort: bring the venv back in line with the reverted commit.
    # Extra packages left over from the bad commit are harmless and NOT
    # worth chasing with a venv rebuild on a solo hobbyist box.
    timeout "$PIP_TIMEOUT_S" "$REPO_DIR/venv/bin/pip" install -r "$REPO_DIR/requirements.txt" >/dev/null 2>&1 || true
    echo "DEPLOY FAILED: restarting $SERVICE on rolled-back $PREV_SHA"
    if ! timeout "$RESTART_TIMEOUT_S" sudo systemctl restart "$SERVICE"; then
        echo "DEPLOY FAILED: rollback restart also failed — $SERVICE may be down, manual intervention required"
    fi
    echo "$NEW_SHA" > "$BAD_SHA_MARKER"
    echo "DEPLOY FAILED: marked $NEW_SHA as bad — will not redeploy it until origin/main moves past it"
    exit 1
}

# Fetch without merging first to check if there's anything new
git fetch origin main --quiet

REMOTE_SHA="$(git rev-parse origin/main)"

if [ -f "$BAD_SHA_MARKER" ]; then
    MARKED_SHA="$(cat "$BAD_SHA_MARKER" 2>/dev/null || true)"
    if [ -n "$MARKED_SHA" ] && [ "$MARKED_SHA" = "$REMOTE_SHA" ]; then
        echo "Skipping: origin/main ($REMOTE_SHA) is marked bad from a previous failed deploy. Push a fix to redeploy."
        exit 0
    fi
    # Remote has moved past the previously-bad commit — clear the marker so
    # a fresh attempt is made.
    echo "origin/main has moved past the previously-marked-bad commit; clearing marker."
    rm -f "$BAD_SHA_MARKER"
fi

BEHIND=$(git rev-list HEAD..origin/main --count)
if [ "$BEHIND" = "0" ]; then
    echo "Nothing to pull (up to date or local is ahead of remote)"
    exit 0
fi

echo "$BEHIND new commit(s) available on remote:"
git log --oneline HEAD..origin/main

# Pull
git pull origin main --ff-only

NEW_SHA="$(git rev-parse HEAD)"

# --- Gate A: syntax preflight on changed Python files -----------------
CHANGED_PY=()
while IFS= read -r f; do
    [ -n "$f" ] && CHANGED_PY+=("$f")
done < <(git diff --name-only --diff-filter=ACMR "$PREV_SHA" "$NEW_SHA" -- '*.py')

if [ "${#CHANGED_PY[@]}" -gt 0 ]; then
    echo "Syntax preflight: py_compile on ${#CHANGED_PY[@]} changed file(s)..."
    if ! "$REPO_DIR/venv/bin/python" -m py_compile "${CHANGED_PY[@]}"; then
        rollback "py_compile failed on changed files: ${CHANGED_PY[*]}"
    fi
fi

# --- Gate B: dependency install, only if requirements.txt changed -----
if git diff --name-only "$PREV_SHA" "$NEW_SHA" -- requirements.txt | grep -q .; then
    echo "requirements.txt changed — installing dependencies..."
    if ! timeout "$PIP_TIMEOUT_S" "$REPO_DIR/venv/bin/pip" install -r "$REPO_DIR/requirements.txt"; then
        rollback "pip install -r requirements.txt failed"
    fi
fi

# --- Gate C: restart + block until the wake loop is ready -------------
echo "Pull complete. Restarting $SERVICE..."
if ! timeout "$RESTART_TIMEOUT_S" sudo systemctl restart "$SERVICE"; then
    rollback "systemctl restart $SERVICE failed or timed out after ${RESTART_TIMEOUT_S}s"
fi

# --- Gate D: catch crash-immediately-after-ready -----------------------
sleep "$POST_RESTART_SETTLE_S"
if ! systemctl is-active --quiet "$SERVICE"; then
    rollback "$SERVICE is not active ${POST_RESTART_SETTLE_S}s after restart (crashed after startup)"
fi

echo "Deploy verified: $SERVICE is active on $NEW_SHA."

echo "Generating status report..."
if "$REPO_DIR/venv/bin/python" "$REPO_DIR/scripts/generate_status.py"; then
    :
else
    STATUS_RC=$?
    echo "WARNING: generate_status.py failed (rc=$STATUS_RC) — STATUS.md may be stale. Non-fatal, continuing."
fi

# Restart web UI if running
if systemctl is-active --quiet bender-web; then
    sudo systemctl restart bender-web
    echo "Web UI restarted."
fi

echo "Done."
