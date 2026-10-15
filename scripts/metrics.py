"""Lightweight metrics collection for BenderPi.

Writes timer and counter events to logs/metrics.jsonl.
Aggregation happens offline via generate_status.py.

Usage:
    from metrics import metrics
    with metrics.timer("stt_transcribe"):
        result = transcribe(audio)
    metrics.count("intent", intent="GREETING")
"""

import json
import os
import time
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# BENDER_LOG_DIR redirects both this file and logger.py's bender.log. It exists
# for the test suite: the suite imports the real modules and the real singleton,
# so every `metrics.count(...)` a test triggered used to append to the DEVELOPER'S
# OWN logs/metrics.jsonl. That is not merely untidy. The mic-stall tests write
# `wake_loop_stall_reinit` and `wake_loop_stall_exit`, watchdog.py reads the same
# file, and `mic_stall_reinit_threshold` is 3 -- so two suite runs raised a
# "USB mic may be flapping/wedging" alert and an error-severity "mic likely needs
# a physical reseat" against a device that was perfectly healthy (2026-10-14:
# 24 of each in 24h on the dev clone, 0 on the device). A false alert on the
# channel that reports real faults is worse than no alert.
_LOG_DIR = os.environ.get("BENDER_LOG_DIR") or os.path.join(_BASE_DIR, "logs")
_DEFAULT_PATH = os.path.join(_LOG_DIR, "metrics.jsonl")


class MetricsWriter:
    """Thread-safe metrics writer to a JSONL file.

    Rotates the file by size rather than switching to a stdlib
    RotatingFileHandler: _write() already opens/appends per call under
    self._lock, so a rename-based rotation (path -> path.1 -> path.2, drop
    the oldest) is safe to do inline and keeps plain JSONL semantics for
    every consumer (watchdog._load_metrics, generate_status.py, the web UI's
    metrics explorer). watchdog._load_metrics() walks the backups newest-
    first until it passes its lookback cutoff.
    """

    DEFAULT_MAX_BYTES = 10 * 1024 * 1024   # 10MB
    DEFAULT_BACKUP_COUNT = 2               # path.1, path.2

    def __init__(self, path: str = None, max_bytes: int = None, backup_count: int = None):
        self._path = path or _DEFAULT_PATH
        self._max_bytes = self.DEFAULT_MAX_BYTES if max_bytes is None else max_bytes
        self._backup_count = self.DEFAULT_BACKUP_COUNT if backup_count is None else backup_count
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(self._path), exist_ok=True)

    def _rotate_locked(self):
        """Shift path -> .1 -> .2 ..., dropping anything past backup_count.
        Caller must already hold self._lock."""
        for i in range(self._backup_count - 1, 0, -1):
            src = f"{self._path}.{i}"
            dst = f"{self._path}.{i + 1}"
            if os.path.exists(src):
                if os.path.exists(dst):
                    os.remove(dst)
                os.rename(src, dst)
        if self._backup_count > 0:
            dst = f"{self._path}.1"
            if os.path.exists(dst):
                os.remove(dst)
            os.rename(self._path, dst)
        else:
            os.remove(self._path)

    def _write(self, event: dict):
        event["ts"] = datetime.now(timezone.utc).isoformat()
        with self._lock:
            try:
                if os.path.getsize(self._path) >= self._max_bytes:
                    self._rotate_locked()
            except OSError:
                pass  # file doesn't exist yet -- nothing to rotate
            with open(self._path, "a") as f:
                f.write(json.dumps(event) + "\n")

    @contextmanager
    def timer(self, name: str, **tags):
        """Context manager that records elapsed time in ms."""
        start = time.monotonic()
        try:
            yield
        finally:
            elapsed_ms = round((time.monotonic() - start) * 1000, 1)
            self._write({"type": "timer", "name": name, "duration_ms": elapsed_ms, **tags})

    def count(self, name: str, **tags):
        """Record a counter event."""
        self._write({"type": "count", "name": name, **tags})


# Singleton
metrics = MetricsWriter()
