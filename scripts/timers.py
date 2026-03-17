"""Timer and alarm CRUD with file-based persistence.

Usage:
    from timers import create_timer, list_timers, check_fired, dismiss_timer

    t = create_timer("pasta", 600)        # 10-minute timer
    a = create_alarm("meeting", dt)       # alarm at specific time
    active = list_timers()                # active timers with remaining_s
    fired = check_fired()                 # timers past their time
    dismiss_timer(t["id"])                # mark as dismissed
    dismiss_all_fired()                   # dismiss all that have fired
    cancel_timer(t["id"])                 # remove entirely
"""

import json
import os
import threading
import uuid
from datetime import datetime, timezone, timedelta

from logger import get_logger
from metrics import metrics

log = get_logger("timers")

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FILE = os.path.join(_BASE_DIR, "timers.json")
_TMP_FILE = os.path.join(_BASE_DIR, "timers.json.tmp")

_lock = threading.Lock()
_cache: list[dict] | None = None


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def _load() -> list[dict]:
    """Load timers from disk. Returns empty list if file missing."""
    global _cache
    if _cache is not None:
        return _cache
    if not os.path.exists(_FILE):
        _cache = []
        return _cache
    try:
        with open(_FILE, "r") as f:
            _cache = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        log.error("Failed to load %s: %s", _FILE, exc)
        _cache = []
    return _cache


def _save(data: list[dict]) -> None:
    """Atomically write timers to disk."""
    global _cache
    _cache = data
    try:
        with open(_TMP_FILE, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(_TMP_FILE, _FILE)
    except OSError as exc:
        log.error("Failed to save %s: %s", _FILE, exc)


def _gen_id(prefix: str) -> str:
    """Generate an ID like t_a1b2c3d4 or a_a1b2c3d4."""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_timer(label: str, duration_seconds: float) -> dict:
    """Create a countdown timer that fires after duration_seconds."""
    now = datetime.now(timezone.utc)
    fires_at = now + timedelta(seconds=duration_seconds)
    entry = {
        "id": _gen_id("t"),
        "label": label,
        "type": "timer",
        "created": now.isoformat(),
        "fires_at": fires_at.isoformat(),
        "duration_s": duration_seconds,
        "fired": False,
        "dismissed": False,
    }
    with _lock:
        data = _load()
        data.append(entry)
        _save(data)
    log.info("Created timer %s '%s' (%ss)", entry["id"], label, duration_seconds)
    metrics.count("timer_create", label=label, duration_s=duration_seconds)
    return entry


def create_alarm(label: str, fires_at: datetime) -> dict:
    """Create an alarm that fires at a specific datetime."""
    now = datetime.now(timezone.utc)
    entry = {
        "id": _gen_id("a"),
        "label": label,
        "type": "alarm",
        "created": now.isoformat(),
        "fires_at": fires_at.isoformat(),
        "duration_s": None,
        "fired": False,
        "dismissed": False,
    }
    with _lock:
        data = _load()
        data.append(entry)
        _save(data)
    log.info("Created alarm %s '%s' at %s", entry["id"], label, fires_at)
    metrics.count("alarm_create", label=label)
    return entry


def cancel_timer(timer_id: str) -> bool:
    """Remove a timer/alarm entirely. Returns True if found."""
    with _lock:
        data = _load()
        before = len(data)
        data = [t for t in data if t["id"] != timer_id]
        if len(data) == before:
            return False
        _save(data)
    log.info("Cancelled %s", timer_id)
    metrics.count("timer_cancel", timer_id=timer_id)
    return True


def dismiss_timer(timer_id: str) -> bool:
    """Mark a timer/alarm as dismissed. Returns True if found."""
    with _lock:
        data = _load()
        for t in data:
            if t["id"] == timer_id:
                t["dismissed"] = True
                _save(data)
                log.info("Dismissed %s", timer_id)
                metrics.count("timer_dismiss", timer_id=timer_id)
                return True
    return False


def dismiss_all_fired() -> int:
    """Dismiss all timers/alarms that have fired. Returns count dismissed."""
    now = datetime.now(timezone.utc)
    count = 0
    with _lock:
        data = _load()
        for t in data:
            fires_at = datetime.fromisoformat(t["fires_at"])
            if fires_at <= now and not t["dismissed"]:
                t["dismissed"] = True
                count += 1
        if count:
            _save(data)
    if count:
        log.info("Dismissed %d fired timer(s)", count)
        metrics.count("timer_dismiss_all", count=count)
    return count


def list_timers() -> list[dict]:
    """Return all non-dismissed timers, with computed remaining_s.

    Also prunes dismissed timers older than 24h from persistence.
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)

    with _lock:
        data = _load()

        # Prune old dismissed entries
        before = len(data)
        data = [
            t for t in data
            if not (
                t["dismissed"]
                and datetime.fromisoformat(t["fires_at"]) < cutoff
            )
        ]
        if len(data) != before:
            _save(data)

        # Build result: active (not dismissed) timers
        result = []
        for t in data:
            if t["dismissed"]:
                continue
            entry = dict(t)
            fires_at = datetime.fromisoformat(entry["fires_at"])
            entry["remaining_s"] = max(0, (fires_at - now).total_seconds())
            result.append(entry)

    return result


def check_fired() -> list[dict]:
    """Return timers/alarms where fires_at has passed and not dismissed."""
    now = datetime.now(timezone.utc)
    with _lock:
        data = _load()
        fired = []
        for t in data:
            fires_at = datetime.fromisoformat(t["fires_at"])
            if fires_at <= now and not t["dismissed"]:
                fired.append(dict(t))
    if fired:
        log.debug("check_fired: %d timer(s) firing", len(fired))
        metrics.count("timer_fired_check", count=len(fired))
    return fired
