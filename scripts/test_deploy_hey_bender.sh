#!/bin/bash
# Dry-run harness for scripts/deploy_hey_bender.sh — exercises argument
# handling, the held-out ship-gate block, --force / --no-eval, the threshold
# write and the rollback line, with `sudo`/`systemctl`, the HF download and
# the evaluation all stubbed via a PATH shim and a fake venv.
#
# Nothing here touches the real device, the real config, systemd or the network.
#
# Usage: bash scripts/test_deploy_hey_bender.sh
# Exit code: 0 if all scenarios pass, 1 otherwise.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_SH="$SCRIPT_DIR/deploy_hey_bender.sh"

PASS=0
FAIL=0
WORKDIR="$(mktemp -d /tmp/bender-deploy-test.XXXXXX)"
trap 'rm -rf "$WORKDIR"' EXIT

fail() { echo "  FAIL: $*"; FAIL=$((FAIL + 1)); }
ok()   { echo "  ok: $*"; PASS=$((PASS + 1)); }

# A throwaway project: fake venv python, fake models dir, real-shaped config.
setup() {
    local ship="$1"           # yes | no | error
    PROJ="$WORKDIR/proj.$RANDOM"
    mkdir -p "$PROJ/scripts" "$PROJ/venv/bin" "$PROJ/models" "$PROJ/bin"
    cp "$DEPLOY_SH" "$PROJ/scripts/deploy_hey_bender.sh"
    cat > "$PROJ/bender_config.json" <<JSON
{
  "oww_model_path": "models/hey_bender_v0.1.onnx",
  "oww_threshold": 0.1
}
JSON
    # real python, so the inline config edits actually run
    ln -sf "$(command -v python3)" "$PROJ/venv/bin/python"
    # stub eval: writes the gate JSON the deploy script reads
    cat > "$PROJ/scripts/eval_wake_model.py" <<PYEOF
import json, sys
if "$ship" == "error":
    sys.exit(1)
out = sys.argv[sys.argv.index("--json") + 1]
json.dump({"gates": [{"ship": "$ship" == "yes"}]}, open(out, "w"))
print("stub evaluation ran")
PYEOF
    cat > "$PROJ/bin/sudo" <<'EOF'
#!/bin/bash
echo "STUB sudo $*" >> "$RESTART_LOG"
EOF
    chmod +x "$PROJ/bin/sudo"
    RESTART_LOG="$PROJ/restarts.log"
    : > "$RESTART_LOG"
    export RESTART_LOG
    export PATH="$PROJ/bin:$PATH"
}

run() {  # run <args...>; captures stdout+stderr and exit code
    OUT="$(cd "$PROJ" && bash scripts/deploy_hey_bender.sh "$@" 2>&1)"
    RC=$?
}

model_in_config() {
    python3 -c "import json;print(json.load(open('$PROJ/bender_config.json'))['oww_model_path'])"
}
threshold_in_config() {
    python3 -c "import json;print(json.load(open('$PROJ/bender_config.json'))['oww_threshold'])"
}

echo "1. A candidate that passes the gates is deployed"
setup yes
touch "$PROJ/models/hey_bender_v0.2.onnx"
run hey_bender_v0.2.onnx --threshold 0.35
[ $RC -eq 0 ] || fail "exit $RC: $OUT"
[ "$(model_in_config)" = "models/hey_bender_v0.2.onnx" ] || fail "model not switched"
[ "$(threshold_in_config)" = "0.35" ] || fail "threshold not written"
grep -q "restart bender-converse" "$RESTART_LOG" || fail "service not restarted"
[ $FAIL -eq 0 ] && ok "deployed, threshold set, service restarted"

echo "2. A candidate that FAILS the gates is refused, and nothing changes"
BEFORE=$FAIL
setup no
touch "$PROJ/models/bad.onnx"
run bad.onnx --threshold 0.35
[ $RC -ne 0 ] || fail "should have exited non-zero"
[ "$(model_in_config)" = "models/hey_bender_v0.1.onnx" ] || fail "config was modified"
[ "$(threshold_in_config)" = "0.1" ] || fail "threshold was modified"
[ ! -s "$RESTART_LOG" ] || fail "service was restarted on a failing model"
echo "$OUT" | grep -q "Ship gates FAILED" || fail "no explanation printed"
[ $FAIL -eq $BEFORE ] && ok "refused before touching config or service"

echo "3. --force deploys a failing candidate"
BEFORE=$FAIL
setup no
touch "$PROJ/models/bad.onnx"
run bad.onnx --force
[ $RC -eq 0 ] || fail "exit $RC: $OUT"
[ "$(model_in_config)" = "models/bad.onnx" ] || fail "model not switched"
[ $FAIL -eq $BEFORE ] && ok "forced deploy works"

echo "4. --no-eval skips the check entirely"
BEFORE=$FAIL
setup error                     # evaluation would fail if it ran
touch "$PROJ/models/x.onnx"
run x.onnx --no-eval
[ $RC -eq 0 ] || fail "exit $RC: $OUT"
echo "$OUT" | grep -q "Skipping the held-out evaluation" || fail "no skip notice"
[ $FAIL -eq $BEFORE ] && ok "skipped and deployed"

echo "5. An evaluation that cannot run blocks the deploy"
BEFORE=$FAIL
setup error
touch "$PROJ/models/x.onnx"
run x.onnx
[ $RC -ne 0 ] || fail "should have exited non-zero"
[ "$(model_in_config)" = "models/hey_bender_v0.1.onnx" ] || fail "config was modified"
[ $FAIL -eq $BEFORE ] && ok "blocked when the harness itself fails"

echo "6. The rollback line names the PREVIOUS model"
BEFORE=$FAIL
setup yes
touch "$PROJ/models/hey_bender_v0.2.onnx"
run hey_bender_v0.2.onnx --threshold 0.35
echo "$OUT" | grep -q "deploy_hey_bender.sh hey_bender_v0.1.onnx --threshold 0.1" \
    || fail "rollback line wrong: $(echo "$OUT" | grep -A2 Rollback)"
[ $FAIL -eq $BEFORE ] && ok "rollback restores the previous model and threshold"

echo "7. Bad arguments are rejected"
BEFORE=$FAIL
setup yes
run ../../etc/passwd;        [ $RC -ne 0 ] || fail "path accepted"
run model.txt;               [ $RC -ne 0 ] || fail "non-onnx accepted"
run --bogus;                 [ $RC -ne 0 ] || fail "unknown option accepted"
[ "$(model_in_config)" = "models/hey_bender_v0.1.onnx" ] || fail "config was modified"
[ $FAIL -eq $BEFORE ] && ok "paths, extensions and unknown flags refused"

echo ""
echo "passed: $PASS   failed: $FAIL"
[ $FAIL -eq 0 ]
