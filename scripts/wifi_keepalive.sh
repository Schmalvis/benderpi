#!/bin/bash
# Re-join WiFi when NetworkManager has given up on it. Run by
# bender-wifi-keepalive.timer every 2 minutes.
#
# Why this exists (measured 2026-10-29): on 6 of 12 mornings the 07:00 cold
# boot never joined WiFi. NetworkManager logged "association took too long"
# three times, then `config -> failed (reason 'no-secrets')`. A no-secrets
# failure BLOCKS autoconnect until the connection is edited or a secret agent
# appears, so the device stayed offline until the 22:00 shutdown. Signal was
# fine (82%, -34 dBm). Offline, Bender still answers (Halloween mode is
# local-only), but the web UI is unreachable: no STOP button, no puppet.
#
# `nmcli device connect` is a manual activation, which ignores that block and
# picks the device's best saved connection, so no SSID is named here.
#
# Acts only on "disconnected" (30) and "failed" (120). Connected (100) is the
# normal case. Activating (40-90) means NetworkManager is still trying, and
# interrupting it would cost more than waiting. Unavailable (20) means the
# radio is not ready, and a connect would fail anyway.

set -u

DEV="${BENDER_WIFI_DEV:-wlan0}"

state="$(nmcli -t -f GENERAL.STATE device show "$DEV" 2>/dev/null | cut -d: -f2)"
code="${state%% *}"

case "$code" in
    30|120)
        echo "WiFi $DEV is '$state'; re-joining"
        if nmcli -w 60 device connect "$DEV"; then
            echo "WiFi $DEV re-joined"
        else
            echo "WiFi $DEV re-join FAILED; next attempt on the next timer tick"
            exit 1
        fi
        ;;
    *)
        exit 0
        ;;
esac
