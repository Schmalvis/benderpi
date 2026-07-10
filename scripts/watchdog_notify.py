#!/usr/bin/env python3
"""Watchdog alert pusher.

Not a daemon: run_checks() + push non-empty results toward Home Assistant's
notify API, guarded by a cooldown/quiet-hours state file so this doesn't turn
into nightly noise Martin learns to ignore. Intended to be invoked every
15-30 minutes by bender-watchdog.timer (systemd/bender-watchdog.timer);
git pull does *not* install the unit -- see systemd/README or CLAUDE.md.

Also sweeps logs/*.jsonl (conversation logs, NOT metrics.jsonl -- that's
size-rotated separately by metrics.py) older than log_retention_days at the
top of each run, so there's no separate retention unit to install.

Dedup/quiet-hours design (solo hobbyist rig, no on-call rotation):
  - Each alert is keyed by its `check` name. A still-firing alert only
    re-notifies every `watchdog_renotify_hours` (default 12h) -- so a
    persistent condition pings once, not every 15 minutes.
  - An alert that clears (no longer in run_checks()'s output) drops its
    cooldown state immediately, so a fresh recurrence notifies right away
    instead of waiting out the old timer.
  - `watchdog_quiet_hours_start`/`_end` (local time, wraps midnight) suppress
    the actual HA push while state/cooldown bookkeeping still happens. This
    intentionally does NOT special-case `session_liveness`: with
    max_hours_without_session=6 and an overnight gap of ~8h, expect one
    notification shortly after quiet hours end most mornings if Bender
    wasn't used before bed. If that's more noise than signal in practice,
    raise max_hours_without_session in watchdog_config.json rather than
    editing this suppression logic.

Usage: venv/bin/python scripts/watchdog_notify.py
"""

import glob
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import cfg
from logger import get_logger
from watchdog import _load_config, run_checks

log = get_logger("watchdog_notify")

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOGS_DIR = os.path.join(_BASE_DIR, "logs")
_STATE_PATH = os.path.join(_LOGS_DIR, ".watchdog_state.json")


def _sweep_old_logs(retention_days) -> int:
    """Delete conversation logs (logs/YYYY-MM-DD.jsonl) older than
    retention_days. Never touches metrics.jsonl or its rotation backups
    (those are size-rotated by metrics.py, not age-based). Returns the
    number of files removed."""
    try:
        retention_days = float(retention_days)
    except (TypeError, ValueError):
        return 0
    if retention_days <= 0:
        return 0
    cutoff = time.time() - retention_days * 86400
    removed = 0
    for path in glob.glob(os.path.join(_LOGS_DIR, "*.jsonl")):
        if os.path.basename(path).startswith("metrics.jsonl"):
            continue
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        except OSError as e:
            log.warning("Could not remove old log %s: %s", path, e)
    return removed


def _load_state() -> dict:
    try:
        with open(_STATE_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    tmp = _STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, _STATE_PATH)


def _in_quiet_hours(cfg_dict: dict) -> bool:
    start = cfg_dict.get("watchdog_quiet_hours_start")
    end = cfg_dict.get("watchdog_quiet_hours_end")
    if start is None or end is None or start == end:
        return False
    hour = datetime.now().hour  # local time -- meant to track Martin's sleep schedule
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end  # window wraps midnight


def _push_ha_notification(cfg_dict: dict, title: str, message: str) -> bool:
    domain = cfg_dict.get("watchdog_notify_domain", "persistent_notification")
    service = cfg_dict.get("watchdog_notify_service", "create")
    if not cfg.ha_url or not cfg.ha_token:
        log.warning("HA_URL/HA_TOKEN not configured -- cannot push watchdog alert")
        return False
    payload = {"title": title, "message": message}
    req = urllib.request.Request(
        f"{cfg.ha_url.rstrip('/')}/api/services/{domain}/{service}",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {cfg.ha_token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=float(cfg.http_timeout_s)) as r:
            ok = r.status in (200, 201)
            if not ok:
                log.warning("HA notify %s/%s returned status %s", domain, service, r.status)
            return ok
    except urllib.error.HTTPError as e:
        log.warning("HA notify %s/%s failed: HTTP %s %s -- confirm the service exists "
                     "(watchdog_notify_domain/watchdog_notify_service in watchdog_config.json)",
                     domain, service, e.code, e.reason)
        return False
    except Exception as e:
        log.warning("HA notify %s/%s failed: %s", domain, service, e)
        return False


def main() -> int:
    cfg_dict = _load_config()

    removed = _sweep_old_logs(cfg_dict.get("log_retention_days", 90))
    if removed:
        log.info("Retention sweep: removed %d log file(s) past retention", removed)

    alerts = run_checks(config=cfg_dict)
    if not alerts:
        log.info("Watchdog: no alerts")
        return 0

    renotify_hours = float(cfg_dict.get("watchdog_renotify_hours", 12))
    now = datetime.now(timezone.utc)
    state = _load_state()
    active_keys = set()
    to_send = []

    for alert in alerts:
        key = alert.check
        active_keys.add(key)
        last = state.get(key, {}).get("last_notified")
        should_notify = True
        if last:
            try:
                last_dt = datetime.fromisoformat(last)
                if (now - last_dt).total_seconds() < renotify_hours * 3600:
                    should_notify = False
            except Exception:
                pass
        if should_notify:
            to_send.append(alert)

    # An alert that's no longer firing drops its cooldown -- a fresh
    # recurrence after resolution notifies immediately.
    for key in list(state.keys()):
        if key not in active_keys:
            del state[key]

    if not to_send:
        log.info("Watchdog: %d alert(s) active, all within %sh renotify cooldown",
                  len(alerts), renotify_hours)
        _save_state(state)
        return 0

    if _in_quiet_hours(cfg_dict):
        log.info("Watchdog: %d alert(s) ready to notify, suppressed by quiet hours "
                  "(state recorded, will re-check next run)", len(to_send))
        _save_state(state)
        return 0

    title = f"BenderPi: {len(to_send)} alert(s)"
    message = "\n".join(f"[{a.severity.upper()}] {a.message}" for a in to_send)
    sent = _push_ha_notification(cfg_dict, title, message)

    if sent:
        for alert in to_send:
            state[alert.check] = {"last_notified": now.isoformat(), "severity": alert.severity}
        log.info("Watchdog: pushed %d alert(s) to HA", len(to_send))
    else:
        log.error("Watchdog: failed to push %d alert(s) to HA", len(to_send))

    _save_state(state)
    return 0 if sent else 1


if __name__ == "__main__":
    sys.exit(main())
