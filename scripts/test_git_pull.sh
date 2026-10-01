#!/bin/bash
# Dry-run test harness for scripts/git_pull.sh — exercises the deploy gates
# (syntax preflight, dependency install, restart, post-restart health check,
# rollback, bad-SHA marker) against a throwaway local git remote, with
# `sudo`/`systemctl` stubbed via a PATH shim. Does NOT touch the real
# systemd, the real repo, or any network resource.
#
# Usage: bash scripts/test_git_pull.sh
# Exit code: 0 if all scenarios pass, 1 otherwise.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GIT_PULL_SH="$SCRIPT_DIR/git_pull.sh"

WORKDIR="$(mktemp -d /tmp/bender-git-pull-test.XXXXXX)"
FAKEBIN="$WORKDIR/fakebin"
REMOTE="$WORKDIR/remote.git"
REPO="$WORKDIR/repo"
STATE_DIR="$WORKDIR/state"

PASS=0
FAIL=0

cleanup() {
    rm -rf "$WORKDIR"
}
trap cleanup EXIT

fail() {
    echo "  FAIL: $1"
    FAIL=$((FAIL + 1))
}

ok() {
    echo "  ok: $1"
    PASS=$((PASS + 1))
}

setup_fakebin() {
    mkdir -p "$FAKEBIN"

    # sudo: just runs the command directly (systemctl is already stubbed,
    # so no real privilege escalation is needed in tests).
    cat > "$FAKEBIN/sudo" <<'EOF'
#!/bin/bash
exec "$@"
EOF

    # systemctl: fakes "restart <svc>" and "is-active --quiet <svc>" against
    # a state file, honouring FAIL_RESTART / FAIL_ACTIVE_AFTER_RESTART /
    # HANG_RESTART_S from the environment.
    cat > "$FAKEBIN/systemctl" <<EOF
#!/bin/bash
STATE_DIR="$STATE_DIR"
mkdir -p "\$STATE_DIR"
if [ "\$1" = "restart" ]; then
    svc="\$2"
    if [ -n "\${HANG_RESTART_S:-}" ]; then
        sleep "\$HANG_RESTART_S"
    fi
    if [ -n "\${FAIL_RESTART:-}" ]; then
        echo "fake systemctl: restart \$svc failed" >&2
        exit 1
    fi
    if [ -n "\${FAIL_ACTIVE_AFTER_RESTART:-}" ]; then
        echo "inactive" > "\$STATE_DIR/\$svc.state"
    else
        echo "active" > "\$STATE_DIR/\$svc.state"
    fi
    exit 0
elif [ "\$1" = "is-active" ]; then
    svc="\${*: -1}"
    state="\$(cat "\$STATE_DIR/\$svc.state" 2>/dev/null || echo unknown)"
    if [ "\$state" = "active" ]; then
        exit 0
    else
        exit 3
    fi
fi
exit 0
EOF

    chmod +x "$FAKEBIN/sudo" "$FAKEBIN/systemctl"
}

setup_repo() {
    rm -rf "$WORKDIR/remote.git" "$REPO" "$STATE_DIR"
    git init --quiet --bare "$REMOTE"
    git -C "$REMOTE" symbolic-ref HEAD refs/heads/main
    git init --quiet -b main "$REPO"
    (
        cd "$REPO"
        git config user.email "test@example.com"
        git config user.name "Test"
        mkdir -p scripts venv/bin
        cat > requirements.txt <<'EOF'
# test requirements
EOF
        cat > scripts/generate_status.py <<'EOF'
import sys
sys.exit(0)
EOF
        cat > scripts/hello.py <<'EOF'
def hello():
    return "hello"
EOF
        # Tracked, but written on the device by deploy_hey_bender.sh and by the
        # web UI's config editor. That combination froze deploys twice.
        printf '{\n  "oww_model_path": "models/hey_bender_v0.1.onnx"\n}\n' \
            > bender_config.json
        # Real python for py_compile fidelity; a stub for pip (no network).
        ln -sf "$(command -v python3)" venv/bin/python
        cat > venv/bin/pip <<'PIPEOF'
#!/bin/bash
if [ -n "${FAIL_PIP:-}" ]; then
    echo "fake pip: install failed" >&2
    exit 1
fi
exit 0
PIPEOF
        chmod +x venv/bin/pip
        git add -A
        git commit --quiet -m "initial commit"
        git remote add origin "$REMOTE"
        git push --quiet origin main
    )
}

run_git_pull() {
    (
        cd "$REPO"
        PATH="$FAKEBIN:$PATH" \
        BENDER_REPO_DIR="$REPO" \
        BENDER_SERVICE="bender-converse-test" \
        BENDER_RESTART_TIMEOUT_S="${BENDER_RESTART_TIMEOUT_S:-3}" \
        BENDER_PIP_TIMEOUT_S="${BENDER_PIP_TIMEOUT_S:-5}" \
        BENDER_POST_RESTART_SETTLE_S="${BENDER_POST_RESTART_SETTLE_S:-0}" \
        FAIL_PIP="${FAIL_PIP:-}" \
        FAIL_RESTART="${FAIL_RESTART:-}" \
        FAIL_ACTIVE_AFTER_RESTART="${FAIL_ACTIVE_AFTER_RESTART:-}" \
        HANG_RESTART_S="${HANG_RESTART_S:-}" \
        bash "$GIT_PULL_SH"
    )
}

push_commit_to_remote() {
    # Pushes a new commit directly to the bare remote from a scratch clone,
    # simulating "someone pushed to GitHub" without touching $REPO's worktree.
    local msg="$1"
    shift
    local scratch="$WORKDIR/scratch"
    rm -rf "$scratch"
    git clone --quiet "$REMOTE" "$scratch"
    (
        cd "$scratch"
        git config user.email "test@example.com"
        git config user.name "Test"
        "$@"
        git add -A
        git commit --quiet -m "$msg"
        git push --quiet origin main
    )
    rm -rf "$scratch"
}

echo "=== Scenario A: healthy commit deploys cleanly ==="
setup_fakebin
setup_repo
push_commit_to_remote "healthy change" bash -c 'echo "def hello(): return \"hi\"" > scripts/hello.py'
OUT="$(run_git_pull)"; RC=$?
echo "$OUT" | sed 's/^/    /'
if [ "$RC" -eq 0 ]; then ok "exit 0"; else fail "expected exit 0, got $RC"; fi
if echo "$OUT" | grep -q "Deploy verified"; then ok "reports deploy verified"; else fail "missing 'Deploy verified'"; fi
HEAD_SHA="$(cd "$REPO" && git rev-parse HEAD)"
REMOTE_SHA="$(cd "$REPO" && git rev-parse origin/main)"
if [ "$HEAD_SHA" = "$REMOTE_SHA" ]; then ok "HEAD advanced to remote"; else fail "HEAD did not advance"; fi
if [ -f "$REPO/.git_pull_bad_sha" ]; then fail "bad-sha marker unexpectedly present"; else ok "no bad-sha marker"; fi

echo "=== Scenario B: bad syntax rolls back and marks the bad SHA ==="
setup_fakebin
setup_repo
PREV_SHA="$(cd "$REPO" && git rev-parse HEAD)"
push_commit_to_remote "syntax error" bash -c 'echo "def broken(:" > scripts/hello.py'
BAD_SHA="$(cd "$REMOTE" && git rev-parse main)"
OUT="$(run_git_pull)"; RC=$?
echo "$OUT" | sed 's/^/    /'
if [ "$RC" -eq 1 ]; then ok "exit 1"; else fail "expected exit 1, got $RC"; fi
if echo "$OUT" | grep -q "DEPLOY FAILED"; then ok "logs DEPLOY FAILED"; else fail "missing 'DEPLOY FAILED'"; fi
HEAD_SHA="$(cd "$REPO" && git rev-parse HEAD)"
if [ "$HEAD_SHA" = "$PREV_SHA" ]; then ok "HEAD rolled back to previous SHA"; else fail "HEAD not rolled back (got $HEAD_SHA, want $PREV_SHA)"; fi
if [ -f "$REPO/.git_pull_bad_sha" ] && [ "$(cat "$REPO/.git_pull_bad_sha")" = "$BAD_SHA" ]; then
    ok "bad-sha marker written with correct SHA"
else
    fail "bad-sha marker missing or wrong"
fi

echo "=== Scenario B2: re-running against the same bad remote head is skipped ==="
OUT="$(run_git_pull)"; RC=$?
echo "$OUT" | sed 's/^/    /'
if [ "$RC" -eq 0 ]; then ok "exit 0 (skip, not a failure)"; else fail "expected exit 0, got $RC"; fi
if echo "$OUT" | grep -q "Skipping"; then ok "reports skip"; else fail "did not report skip"; fi

echo "=== Scenario B3: a fix pushed on top clears the marker and deploys ==="
push_commit_to_remote "fix syntax" bash -c 'echo "def fixed(): return 1" > scripts/hello.py'
OUT="$(run_git_pull)"; RC=$?
echo "$OUT" | sed 's/^/    /'
if [ "$RC" -eq 0 ]; then ok "exit 0"; else fail "expected exit 0, got $RC"; fi
if [ -f "$REPO/.git_pull_bad_sha" ]; then fail "bad-sha marker not cleared"; else ok "bad-sha marker cleared"; fi

echo "=== Scenario C: pip install failure rolls back ==="
setup_fakebin
setup_repo
PREV_SHA="$(cd "$REPO" && git rev-parse HEAD)"
push_commit_to_remote "bump deps" bash -c 'echo "somepkg==1.0" >> requirements.txt'
OUT="$(FAIL_PIP=1 run_git_pull)"; RC=$?
echo "$OUT" | sed 's/^/    /'
if [ "$RC" -eq 1 ]; then ok "exit 1"; else fail "expected exit 1, got $RC"; fi
HEAD_SHA="$(cd "$REPO" && git rev-parse HEAD)"
if [ "$HEAD_SHA" = "$PREV_SHA" ]; then ok "HEAD rolled back after pip failure"; else fail "HEAD not rolled back"; fi

echo "=== Scenario D: restart failure rolls back ==="
setup_fakebin
setup_repo
PREV_SHA="$(cd "$REPO" && git rev-parse HEAD)"
push_commit_to_remote "harmless change" bash -c 'echo "# comment" >> scripts/hello.py'
OUT="$(FAIL_RESTART=1 run_git_pull)"; RC=$?
echo "$OUT" | sed 's/^/    /'
if [ "$RC" -eq 1 ]; then ok "exit 1"; else fail "expected exit 1, got $RC"; fi
HEAD_SHA="$(cd "$REPO" && git rev-parse HEAD)"
if [ "$HEAD_SHA" = "$PREV_SHA" ]; then ok "HEAD rolled back after restart failure"; else fail "HEAD not rolled back"; fi

echo "=== Scenario E: crash-after-ready (is-active fails post-restart) rolls back ==="
setup_fakebin
setup_repo
PREV_SHA="$(cd "$REPO" && git rev-parse HEAD)"
push_commit_to_remote "harmless change 2" bash -c 'echo "# comment2" >> scripts/hello.py'
OUT="$(FAIL_ACTIVE_AFTER_RESTART=1 run_git_pull)"; RC=$?
echo "$OUT" | sed 's/^/    /'
if [ "$RC" -eq 1 ]; then ok "exit 1"; else fail "expected exit 1, got $RC"; fi
HEAD_SHA="$(cd "$REPO" && git rev-parse HEAD)"
if [ "$HEAD_SHA" = "$PREV_SHA" ]; then ok "HEAD rolled back after crash-after-ready"; else fail "HEAD not rolled back"; fi

echo "=== Scenario F: hung restart is bounded by the timeout ==="
setup_fakebin
setup_repo
PREV_SHA="$(cd "$REPO" && git rev-parse HEAD)"
push_commit_to_remote "harmless change 3" bash -c 'echo "# comment3" >> scripts/hello.py'
START=$(date +%s)
OUT="$(HANG_RESTART_S=15 BENDER_RESTART_TIMEOUT_S=2 run_git_pull)"; RC=$?
END=$(date +%s)
ELAPSED=$((END - START))
echo "$OUT" | sed 's/^/    /'
if [ "$RC" -eq 1 ]; then ok "exit 1"; else fail "expected exit 1, got $RC"; fi
if [ "$ELAPSED" -lt 15 ]; then ok "bounded by timeout (${ELAPSED}s, did not wait full hang)"; else fail "did not bound the hang (${ELAPSED}s)"; fi
HEAD_SHA="$(cd "$REPO" && git rev-parse HEAD)"
if [ "$HEAD_SHA" = "$PREV_SHA" ]; then ok "HEAD rolled back after hung restart"; else fail "HEAD not rolled back"; fi

echo "A device-modified bender_config.json does not block the pull"
BEFORE=$FAIL
setup_repo
# the device switched its own wake model, which is what deploy_hey_bender.sh does
printf '{\n  "oww_model_path": "models/hey_bender_v0.3_r35.onnx"\n}\n' \
    > "$REPO/bender_config.json"
# the incoming commit also touches that file — the conflicting case that froze
# deploys on 2026-09-28 and 2026-10-01
push_commit_to_remote "config + code change" bash -c \
    'printf "{\n  \"oww_model_path\": \"models/hey_bender_v0.1.onnx\",\n  \"new_key\": 7\n}\n" > bender_config.json; echo "def f(): return 1" > scripts/hello.py'
OUT_CFG="$(run_git_pull 2>&1)"
RC=$?
[ $RC -eq 0 ] || fail "pull should have succeeded, exit $RC: $(echo "$OUT_CFG" | tail -6)"
grep -q "hey_bender_v0.3_r35" "$REPO/bender_config.json" \
    || fail "the DEVICE's config value must survive the pull"
grep -q "def f()" "$REPO/scripts/hello.py" \
    || fail "the code change must still land"
[ $FAIL -eq $BEFORE ] && ok "device config preserved, code still updated"

echo "A gate rollback does not revert the device's config"
BEFORE=$FAIL
setup_repo
printf '{\n  "halloween_mode": true\n}\n' > "$REPO/bender_config.json"
# a commit that changes the config AND fails the restart gate
push_commit_to_remote "config change + bad restart" bash -c \
    'printf "{\n  \"halloween_mode\": false\n}\n" > bender_config.json; echo "x = 1" > scripts/hello.py'
FAIL_RESTART=1 run_git_pull >/dev/null 2>&1
grep -q '"halloween_mode": true' "$REPO/bender_config.json" \
    || fail "rollback reverted the device's config: $(cat "$REPO/bender_config.json")"
[ $FAIL -eq $BEFORE ] && ok "device config survives a rollback"

echo "A failed pull does not revert the device's config"
BEFORE=$FAIL
setup_repo
printf '{\n  "halloween_mode": true\n}\n' > "$REPO/bender_config.json"
# a second dirty tracked file makes the pull itself abort
echo "locally edited" >> "$REPO/requirements.txt"
push_commit_to_remote "touches both files" bash -c \
    'printf "{\n  \"halloween_mode\": false\n}\n" > bender_config.json; echo "# upstream" >> requirements.txt'
run_git_pull >/dev/null 2>&1
grep -q '"halloween_mode": true' "$REPO/bender_config.json" \
    || fail "a failed pull left the tracked template: $(cat "$REPO/bender_config.json")"
[ $FAIL -eq $BEFORE ] && ok "device config survives a failed pull"

echo "A device-modified index.json does not freeze the deploy"
BEFORE=$FAIL
setup_repo
# first make it a TRACKED artifact, as it is in the real repo
push_commit_to_remote "track the index" bash -c \
    'mkdir -p speech/responses; printf "{\n  \"halloween\": [\"base\"]\n}\n" > speech/responses/index.json'
run_git_pull >/dev/null 2>&1
# now the device runs prebuild_responses.py, dirtying it
printf '{\n  "halloween": ["old"]\n}\n' > "$REPO/speech/responses/index.json"
push_commit_to_remote "new index" bash -c \
    'printf "{\n  \"halloween\": [\"new\"]\n}\n" > speech/responses/index.json'
run_git_pull >/dev/null 2>&1
grep -q '"new"' "$REPO/speech/responses/index.json" \
    || fail "the repo version must win for index.json: $(cat "$REPO/speech/responses/index.json")"
[ "$(cd "$REPO" && git rev-parse HEAD)" = "$(cd "$REMOTE" && git rev-parse HEAD)" ] \
    || fail "the pull was still blocked by the dirty index.json"
[ $FAIL -eq $BEFORE ] && ok "a dirty index.json is discarded, not a freeze"

echo "An UNTRACKED index.json does not freeze the deploy either (fresh Pi)"
BEFORE=$FAIL
setup_repo
mkdir -p "$REPO/speech/responses"
# fresh clone, prebuild run locally, THEN a commit starts tracking the file
printf '{\n  "halloween": ["local"]\n}\n' > "$REPO/speech/responses/index.json"
push_commit_to_remote "start tracking the index" bash -c \
    'mkdir -p speech/responses; printf "{\n  \"halloween\": [\"tracked\"]\n}\n" > speech/responses/index.json'
run_git_pull >/dev/null 2>&1
[ "$(cd "$REPO" && git rev-parse HEAD)" = "$(cd "$REMOTE" && git rev-parse HEAD)" ] \
    || fail "an untracked index.json still blocked the pull"
grep -q '"tracked"' "$REPO/speech/responses/index.json" \
    || fail "the newly tracked index.json did not land"
[ $FAIL -eq $BEFORE ] && ok "an untracked index.json is cleared, not a freeze"

echo
echo "=== Results: $PASS passed, $FAIL failed ==="
if [ "$FAIL" -ne 0 ]; then
    exit 1
fi
exit 0
