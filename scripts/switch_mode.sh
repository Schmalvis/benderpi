#!/bin/bash
# switch_mode.sh — Switch Bender between operating modes.
#
# Modes:
#   clips    — bender-wakeword.service  (pre-recorded Bender clips)
#   tts      — bender-tts.service       (Piper TTS, static responses)
#   converse — bender-converse.service  (full AI conversation mode)
#
# Usage: scripts/switch_mode.sh [clips|tts|converse|status]

CLIP_SVC="bender-wakeword"
TTS_SVC="bender-tts"
CONV_SVC="bender-converse"

ALL_SVCS="$CLIP_SVC $TTS_SVC $CONV_SVC"

status() {
    for svc in $ALL_SVCS; do
        STATE=$(systemctl is-active $svc 2>/dev/null)
        printf "  %-30s %s\n" "$svc:" "$STATE"
    done
}

stop_all() {
    for svc in $ALL_SVCS; do
        sudo systemctl stop $svc 2>/dev/null
    done
}

use_clips() {
    stop_all
    sudo systemctl start $CLIP_SVC
    echo "Switched to CLIP mode ($CLIP_SVC)"
}

use_tts() {
    stop_all
    sudo systemctl start $TTS_SVC
    echo "Switched to TTS mode ($TTS_SVC)"
}

use_converse() {
    stop_all
    sudo systemctl start $CONV_SVC
    echo "Switched to CONVERSE mode ($CONV_SVC)"
}

case "${1:-status}" in
    clips)    use_clips ;;
    tts)      use_tts ;;
    converse) use_converse ;;
    status)   status ;;
    *)        echo "Usage: $0 [clips|tts|converse|status]"; exit 1 ;;
esac
